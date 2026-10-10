import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.wallets.models import LedgerEntry, Wallet


class TopUp(models.Model):
    """Carregamento da carteira por referência (StrongPay / AppyPay)."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        PAID = "paid", _("Paid")
        FAILED = "failed", _("Failed")
        CANCELLED = "cancelled", _("Cancelled")
        EXPIRED = "expired", _("Expired")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name="topups")
    amount_kz = models.PositiveIntegerField(_("amount (Kz)"))
    status = models.CharField(
        _("status"), max_length=10, choices=Status.choices, default=Status.PENDING
    )
    provider = models.CharField(_("provider"), max_length=20)
    provider_payment_id = models.CharField(_("provider payment ID"), max_length=64, unique=True)
    transaction_id = models.CharField(_("transaction ID"), max_length=64, blank=True)
    entity = models.CharField(_("entity"), max_length=10, blank=True)
    reference = models.CharField(_("reference"), max_length=20, blank=True)
    expires_at = models.DateTimeField(_("expires at"), null=True, blank=True)
    idempotency_key = models.CharField(max_length=80, unique=True)
    paid_amount_kz = models.PositiveIntegerField(_("paid amount (Kz)"), null=True, blank=True)
    paid_at = models.DateTimeField(_("paid at"), null=True, blank=True)
    ledger_entry = models.OneToOneField(
        LedgerEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="topup"
    )
    raw_payload = models.JSONField(_("provider response"), default=dict, blank=True)
    events = models.JSONField(_("provider events"), default=list, blank=True)
    created_by = models.CharField(_("created by"), max_length=100, blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("wallet top-up")
        verbose_name_plural = _("wallet top-ups")
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "created_at"], name="topup_status_idx")]

    def __str__(self):
        return f"{self.amount_kz} Kz · {self.entity}/{self.reference} [{self.status}]"


class WebhookEvent(models.Model):
    """Registo de todos os webhooks recebidos (auditoria e idempotência)."""

    provider = models.CharField(max_length=20)
    body_sha256 = models.CharField(max_length=64, unique=True)
    headers = models.JSONField(default=dict)
    body = models.TextField()
    valid = models.BooleanField(default=False)
    result = models.CharField(max_length=40, blank=True)
    topup = models.ForeignKey(
        TopUp, null=True, blank=True, on_delete=models.SET_NULL, related_name="webhook_events"
    )
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("webhook event")
        verbose_name_plural = _("webhook events")
        ordering = ["-received_at"]

    def __str__(self):
        return f"{self.provider} {self.result} @ {self.received_at:%Y-%m-%d %H:%M}"
