"""Alertas por e-mail quando um kiosk fica parado e quando volta a funcionar.

- parado há ``DOWNTIME_ALERT_AFTER_SECONDS`` seguidos → um e-mail com o motivo;
- o motivo muda enquanto continua parado → novo e-mail (o novo período também espera);
- volta a atender → um e-mail com quanto tempo esteve parado;
- manutenção posta pelo dono não avisa; no máximo ``DOWNTIME_ALERT_MAX_PER_HOUR`` por kiosk.
"""

from datetime import timedelta

import structlog
from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.utils import timezone

from .models import Downtime, format_duration

log = structlog.get_logger(__name__)
R = Downtime.Reason

REASON_TEXT = {
    R.KIOSK_OFFLINE: "Pi sem sinal (sem internet ou desligado)",
    R.NO_PRINTER: "Sem impressora configurada",
    R.OFFLINE: "Impressora desligada ou inacessível (pode ser a VPN)",
    R.PAPER_OUT: "Sem papel",
    R.PAPER_JAM: "Papel encravado",
    R.DOOR_OPEN: "Porta da impressora aberta",
    R.TONER_EMPTY: "Toner vazio",
    R.ERROR: "Erro da impressora",
}


def _when(dt) -> str:
    return timezone.localtime(dt).strftime("%d/%m/%Y %H:%M")


def chain_start(downtime: Downtime):
    """Início da paragem: períodos seguidos (o motivo mudou sem o kiosk voltar) contam juntos."""
    start = downtime
    for _ in range(50):
        previous = (
            Downtime.objects.filter(kiosk_id=start.kiosk_id, ended_at=start.started_at)
            .exclude(pk=start.pk)
            .exclude(reason=R.MAINTENANCE)
            .first()
        )
        if previous is None:
            break
        start = previous
    return start.started_at


def history_url(kiosk) -> str:
    if not settings.HOST_ADMIN:
        return ""
    return f"https://{settings.HOST_ADMIN}/admin/monitoring/downtime/?kiosk__id__exact={kiosk.pk}"


def _allowed(kiosk) -> bool:
    key = f"downtime-alert:{kiosk.pk}"
    if cache.add(key, 1, 3600):
        return True
    try:
        return cache.incr(key) <= settings.DOWNTIME_ALERT_MAX_PER_HOUR
    except ValueError:
        cache.set(key, 1, 3600)
        return True


def _send(kiosk, subject: str, lines: list[str]) -> bool:
    """Envia (ou salta, se o limite por hora foi atingido). False = falhou, tentar depois."""
    if not _allowed(kiosk):
        log.warning("downtime_alert.rate_limited", kiosk=kiosk.code, subject=subject)
        return True
    url = history_url(kiosk)
    body = "\n".join(lines + ([f"\nHistórico: {url}"] if url else []))
    try:
        send_mail(subject, body, None, settings.ALERT_EMAILS)
    except Exception as exc:  # SMTP em baixo: tenta no próximo minuto
        log.error("downtime_alert.send_failed", kiosk=kiosk.code, error=str(exc)[:200])
        return False
    log.info("downtime_alert.sent", kiosk=kiosk.code, subject=subject)
    return True


def _place(kiosk) -> str:
    site = kiosk.site
    return f"{site.partner.name} · {site.name} · {kiosk.name}"


def down_message(downtime: Downtime, now) -> tuple[str, list[str]]:
    kiosk, printer = downtime.kiosk, downtime.printer
    reason = REASON_TEXT.get(downtime.reason, downtime.get_reason_display())
    since = chain_start(downtime)
    lines = [
        f"O kiosk {kiosk.code} ({_place(kiosk)}) não está a atender clientes.",
        "",
        f"Motivo: {reason}",
        f"Parado desde: {_when(since)} (há {format_duration(now - since)})",
    ]
    if printer is not None:
        said = ", ".join((downtime.detail or {}).get("reasons", [])) or "—"
        lines.append(f"Impressora: {printer.name} — estado {printer.status} ({said})")
        device = getattr(printer, "edge_device", None)
        if device is not None and device.last_heartbeat:
            lines.append(f"Último contacto do Pi: {_when(device.last_heartbeat)}")
    return f"⚠️ Kiosk {kiosk.code} parado — {reason}", lines


def up_message(kiosk, since, until) -> tuple[str, list[str]]:
    took = format_duration(until - since)
    lines = [
        f"O kiosk {kiosk.code} ({_place(kiosk)}) voltou a atender clientes.",
        "",
        f"Parado de {_when(since)} a {_when(until)} ({took}).",
    ]
    return f"✅ Kiosk {kiosk.code} voltou a funcionar (parado {took})", lines


def notify_downtimes(now=None) -> int:
    """Corre a cada minuto. Devolve quantos e-mails foram enviados (ou saltados pelo limite)."""
    if not settings.ALERT_EMAILS:
        return 0
    now = now or timezone.now()
    sent = 0

    late = now - timedelta(seconds=settings.DOWNTIME_ALERT_AFTER_SECONDS)
    pending = (
        Downtime.objects.filter(
            ended_at__isnull=True, notified_at__isnull=True, started_at__lte=late
        )
        .exclude(reason=R.MAINTENANCE)
        .select_related("kiosk__site__partner", "printer__edge_device")
    )
    for downtime in pending:
        subject, lines = down_message(downtime, now)
        if _send(downtime.kiosk, subject, lines):
            Downtime.objects.filter(pk=downtime.pk).update(notified_at=now)
            sent += 1

    ended = Downtime.objects.filter(
        ended_at__isnull=False, notified_at__isnull=False, recovery_notified_at__isnull=True
    ).select_related("kiosk__site__partner")
    by_kiosk: dict[int, list[Downtime]] = {}
    for downtime in ended:
        by_kiosk.setdefault(downtime.kiosk_id, []).append(downtime)
    for downtimes in by_kiosk.values():
        kiosk = downtimes[0].kiosk
        current = Downtime.objects.filter(kiosk=kiosk, ended_at__isnull=True).first()
        if current is not None and current.reason != R.MAINTENANCE and current.started_at > late:
            continue  # parou por outro motivo há pouco: espera para ver se volta ou se avisa
        ids = [d.pk for d in downtimes]
        if current is None:
            first = min(downtimes, key=lambda d: d.started_at)
            until = (
                Downtime.objects.filter(kiosk=kiosk, ended_at__isnull=False)
                .order_by("-ended_at")
                .values_list("ended_at", flat=True)
                .first()
            )
            subject, lines = up_message(kiosk, chain_start(first), until)
            if not _send(kiosk, subject, lines):
                continue
            sent += 1
        # Ainda parado (outro motivo, já avisado) ou em manutenção: sem e-mail de "voltou".
        Downtime.objects.filter(pk__in=ids).update(recovery_notified_at=now)
    return sent
