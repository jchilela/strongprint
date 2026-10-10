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
