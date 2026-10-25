from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.devices.models import Printer
from apps.partners.models import Kiosk


class Alert(models.Model):
    class Kind(models.TextChoices):
        PAPER_OUT = "paper_out", _("Out of paper")
        TONER_LOW = "toner_low", _("Toner low")
        PRINTER_ERROR = "printer_error", _("Printer error")
        PRINTER_OFFLINE = "printer_offline", _("Printer offline")
        KIOSK_OFFLINE = "kiosk_offline", _("Kiosk offline")
        UNEXPECTED_PAGES = "unexpected_pages", _("Pages printed outside StrongPrint")

    class Severity(models.TextChoices):
        INFO = "info", _("Info")
        WARNING = "warning", _("Warning")
        CRITICAL = "critical", _("Critical")

    kind = models.CharField(_("kind"), max_length=20, choices=Kind.choices)
    severity = models.CharField(_("severity"), max_length=10, choices=Severity.choices)
    kiosk = models.ForeignKey(Kiosk, on_delete=models.CASCADE, related_name="alerts")
    printer = models.ForeignKey(
        Printer, null=True, blank=True, on_delete=models.CASCADE, related_name="alerts"
    )
    message = models.CharField(_("message"), max_length=255)
    data = models.JSONField(_("data"), default=dict, blank=True)
    opened_at = models.DateTimeField(_("opened at"), auto_now_add=True)
    last_seen_at = models.DateTimeField(_("last seen at"), auto_now=True)
    resolved_at = models.DateTimeField(_("resolved at"), null=True, blank=True)

    class Meta:
        verbose_name = _("alert")
        verbose_name_plural = _("alerts")
        ordering = ["-opened_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "kiosk"],
                condition=Q(resolved_at__isnull=True),
                name="uniq_open_alert",
            ),
        ]

    def __str__(self):
        return f"[{self.severity}] {self.kiosk} — {self.get_kind_display()}"

    @property
    def is_open(self) -> bool:
        return self.resolved_at is None


def format_duration(delta) -> str:
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds} s"
    hours, minutes = divmod(seconds // 60, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days} d {hours} h"
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min"


class Downtime(models.Model):
    """Período em que um kiosk não pôde atender clientes, e porquê.

    Há no máximo um período aberto por kiosk; mudar de motivo fecha o atual e abre outro.
    """

    class Reason(models.TextChoices):
        MAINTENANCE = "maintenance", _("Maintenance / out of service")
        KIOSK_OFFLINE = "kiosk_offline", _("Kiosk offline (no signal)")
        NO_PRINTER = "no_printer", _("No printer")
        OFFLINE = "offline", _("Printer off or unreachable")
        PAPER_OUT = "paper_out", _("Out of paper")
        PAPER_JAM = "paper_jam", _("Paper jam")
        DOOR_OPEN = "door_open", _("Printer door open")
        TONER_EMPTY = "toner_empty", _("Toner empty")
        ERROR = "error", _("Printer error")

    kiosk = models.ForeignKey(Kiosk, on_delete=models.CASCADE, related_name="downtimes")
    printer = models.ForeignKey(
        Printer, null=True, blank=True, on_delete=models.SET_NULL, related_name="downtimes"
    )
    reason = models.CharField(_("reason"), max_length=20, choices=Reason.choices)
    detail = models.JSONField(_("printer detail"), default=dict, blank=True)
    started_at = models.DateTimeField(_("started at"), db_index=True)
    ended_at = models.DateTimeField(_("ended at"), null=True, blank=True)
    notified_at = models.DateTimeField(_("alert e-mail sent at"), null=True, blank=True)
    recovery_notified_at = models.DateTimeField(_("recovery e-mail sent at"), null=True, blank=True)

    class Meta:
        verbose_name = _("unavailability")
        verbose_name_plural = _("unavailability history")
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["kiosk"],
                condition=Q(ended_at__isnull=True),
                name="uniq_open_downtime",
            ),
        ]

    def __str__(self):
        return f"{self.kiosk} — {self.get_reason_display()}"

    @property
    def is_open(self) -> bool:
        return self.ended_at is None

    @property
    def duration(self):
        from django.utils import timezone

        return (self.ended_at or timezone.now()) - self.started_at

    @property
    def duration_text(self) -> str:
        return format_duration(self.duration)
