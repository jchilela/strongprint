"""Alertas: um alerta aberto por (tipo, kiosk); abre/atualiza e fecha automaticamente.

Por agora os alertas ficam no modelo + log estruturado. Notificações (WhatsApp/SMS/
e-mail) ligam-se depois a ``alert.opened``.
"""

from datetime import timedelta

import structlog
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.devices.models import Printer
from apps.jobs.models import Job
from apps.partners.models import Kiosk
from domain.jobs.state_machine import JobState
from domain.printing.blocking import block_reason
from domain.printing.gateway import PrinterState, PrinterUnavailable

from .models import Alert, Downtime

log = structlog.get_logger(__name__)
K, S = Alert.Kind, Alert.Severity


def open_alert(kind, kiosk, *, severity, message, printer=None, data=None) -> Alert:
    alert = Alert.objects.filter(kind=kind, kiosk=kiosk, resolved_at__isnull=True).first()
    if alert:
        alert.message, alert.data, alert.severity = message, data or {}, severity
        alert.save(update_fields=["message", "data", "severity", "last_seen_at"])
        return alert
    try:
        with transaction.atomic():
            alert = Alert.objects.create(
                kind=kind,
                kiosk=kiosk,
                printer=printer,
                severity=severity,
                message=message,
                data=data or {},
            )
    except IntegrityError:
        return Alert.objects.get(kind=kind, kiosk=kiosk, resolved_at__isnull=True)
    log.warning(
        "alert.opened",
        kind=kind,
        severity=severity,
        kiosk=kiosk.code,
        message=message,
        data=data or {},
    )
    return alert


def resolve_alert(kind, kiosk) -> int:
    count = Alert.objects.filter(kind=kind, kiosk=kiosk, resolved_at__isnull=True).update(
        resolved_at=timezone.now()
    )
    if count:
        log.info("alert.resolved", kind=kind, kiosk=kiosk.code)
    return count


def set_alert(active: bool, kind, kiosk, **kwargs):
    if active:
        return open_alert(kind, kiosk, **kwargs)
    resolve_alert(kind, kiosk)
    return None


def poll_printer(printer: Printer) -> Printer:
    """Lê estado + contador, guarda-os e abre/fecha alertas do kiosk."""
    from apps.jobs.printing import gateway_for

    kiosk = printer.kiosk
    gateway = gateway_for(printer)
    now = timezone.now()
    try:
        status = gateway.get_status()
    except PrinterUnavailable as exc:
        printer.status = Printer.Status.OFFLINE
        printer.status_detail = {"error": str(exc)[:200]}
        printer.last_polled_at = now
        printer.save(update_fields=["status", "status_detail", "last_polled_at", "updated_at"])
        open_alert(
            K.PRINTER_OFFLINE,
            kiosk,
            severity=S.CRITICAL,
            printer=printer,
            message=f"{printer.name} is unreachable",
            data={"error": str(exc)[:200]},
        )
        record_availability(kiosk, printer)
        return printer

    resolve_alert(K.PRINTER_OFFLINE, kiosk)
    counter = gateway.get_page_counter()
    check_unexpected_pages(printer, counter)

    printer.status = status.state.value
    printer.status_detail = status.to_dict()
    printer.last_polled_at = now
    if counter is not None:
        printer.last_page_counter = counter
    printer.save(
        update_fields=[
            "status",
            "status_detail",
            "last_polled_at",
            "last_page_counter",
            "updated_at",
        ]
    )

    set_alert(
        status.paper_out,
        K.PAPER_OUT,
        kiosk,
        severity=S.CRITICAL,
        printer=printer,
        message=f"{printer.name}: out of paper",
    )
    low = {c: lvl for c, lvl in status.toner.items() if 0 <= lvl < settings.TONER_LOW_PCT}
    set_alert(
        bool(low),
        K.TONER_LOW,
        kiosk,
        severity=S.WARNING,
        printer=printer,
        message=f"{printer.name}: toner low ({', '.join(f'{c} {v}%' for c, v in low.items())})",
        data={"toner": low},
    )
    set_alert(
        status.state == PrinterState.ERROR and not status.paper_out,
        K.PRINTER_ERROR,
        kiosk,
        severity=S.CRITICAL,
        printer=printer,
        message=f"{printer.name}: {', '.join(status.reasons) or 'error'}",
        data={"reasons": status.reasons},
    )
    record_availability(kiosk, printer)
    return printer


def check_unexpected_pages(printer: Printer, counter: int | None):
    """Com o painel desligado, o contador só deve subir durante trabalhos StrongPrint."""
    previous = printer.last_page_counter
    if counter is None or previous is None or counter <= previous or printer.panel_enabled:
        return
    busy = Job.objects.filter(printer=printer, state=JobState.PRINTING).exists()
    recent = Job.objects.filter(
        printer=printer, finished_at__gte=printer.last_polled_at or timezone.now()
    ).exists()
    if busy or recent:
        return
    open_alert(
        K.UNEXPECTED_PAGES,
        printer.kiosk,
        severity=S.WARNING,
        printer=printer,
        message=f"{printer.name}: {counter - previous} page(s) printed outside StrongPrint",
        data={"from": previous, "to": counter},
    )


def mark_offline_kiosks() -> int:
    """Kiosks ativos sem heartbeat há mais de N segundos → OFFLINE + alerta."""
    limit = timezone.now() - timedelta(seconds=settings.KIOSK_OFFLINE_AFTER_SECONDS)
    count = 0
    kiosks = Kiosk.objects.filter(is_active=True, status=Kiosk.Status.ONLINE).select_related(
        "printer__edge_device"
    )
    for kiosk in kiosks:
        device = getattr(getattr(kiosk, "printer", None), "edge_device", None)
        last = device.last_heartbeat if device else None
        if last is None or last < limit:
            Kiosk.objects.filter(pk=kiosk.pk).update(status=Kiosk.Status.OFFLINE)
            open_alert(
                K.KIOSK_OFFLINE,
                kiosk,
                severity=S.CRITICAL,
                message=f"{kiosk.code}: no heartbeat since {last:%H:%M:%S}"
                if last
                else "never seen",
                data={"last_heartbeat": last.isoformat() if last else None},
            )
            count += 1
    return count


# --- histórico de indisponibilidade --------------------------------------------------


def availability_reason(kiosk, printer=None) -> str | None:
    """Motivo pelo qual o kiosk não atende agora (None = disponível)."""
    if not kiosk.is_active or kiosk.status in (
        Kiosk.Status.MAINTENANCE,
        Kiosk.Status.OUT_OF_SERVICE,
    ):
        return Downtime.Reason.MAINTENANCE
    if kiosk.status == Kiosk.Status.OFFLINE:
        return Downtime.Reason.KIOSK_OFFLINE
    printer = printer or getattr(kiosk, "printer", None)
    if printer is None:
        return Downtime.Reason.NO_PRINTER
    return block_reason(printer.status, printer.status_detail)


def record_availability(kiosk, printer=None) -> Downtime | None:
    """Abre/fecha o período de indisponibilidade do kiosk conforme o estado atual."""
    kiosk.refresh_from_db(fields=["status", "is_active"])
    printer = printer or getattr(kiosk, "printer", None)
    reason = availability_reason(kiosk, printer)
    now = timezone.now()
    current = Downtime.objects.filter(kiosk=kiosk, ended_at__isnull=True).first()
    if current and current.reason == reason:
        return current
    if current:
        Downtime.objects.filter(pk=current.pk).update(ended_at=now)
        log.info("kiosk.available" if not reason else "kiosk.downtime_changed",
                 kiosk=kiosk.code, previous=current.reason, reason=reason)
    if not reason:
        return None
    detail = {}
    if printer is not None:
        detail = {
            "status": printer.status,
            "reasons": (printer.status_detail or {}).get("reasons", []),
        }
    try:
        with transaction.atomic():
            downtime = Downtime.objects.create(
                kiosk=kiosk, printer=printer, reason=reason, detail=detail, started_at=now
            )
    except IntegrityError:
        return Downtime.objects.filter(kiosk=kiosk, ended_at__isnull=True).first()
    log.warning("kiosk.unavailable", kiosk=kiosk.code, reason=reason, detail=detail)
    return downtime


def record_all_availability() -> int:
    count = 0
    for kiosk in Kiosk.objects.select_related("printer"):
        try:
            record_availability(kiosk)
            count += 1
        except Exception:
            log.exception("monitor.availability_failed", kiosk=kiosk.code)
    return count
