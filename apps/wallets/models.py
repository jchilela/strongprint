from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

from apps.jobs.models import Job
from apps.partners.models import Kiosk


class Wallet(models.Model):
    """Carteira do cliente, válida em qualquer kiosk StrongPrint.

    ``balance_kz`` é o saldo total (igual à soma do livro-razão); ``held_kz`` é
    a parte cativa por trabalhos em curso. Disponível = balance − held.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="wallet"
    )
    balance_kz = models.BigIntegerField(_("balance (Kz)"), default=0)
    held_kz = models.BigIntegerField(_("held (Kz)"), default=0)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("wallet")
        verbose_name_plural = _("wallets")
        constraints = [
            models.CheckConstraint(
                name="wallet_balance_non_negative", condition=Q(balance_kz__gte=0)
            ),
            models.CheckConstraint(name="wallet_held_non_negative", condition=Q(held_kz__gte=0)),
            models.CheckConstraint(
                name="wallet_held_within_balance", condition=Q(held_kz__lte=F("balance_kz"))
            ),
        ]

    def __str__(self):
        return f"{self.user} — {self.balance_kz} Kz"

    @property
    def available_kz(self) -> int:
        return self.balance_kz - self.held_kz


class LedgerEntry(models.Model):
    """Movimento de dinheiro real numa carteira. Imutável."""

    class Kind(models.TextChoices):
        TOPUP = "TOPUP", _("Top-up")
        CAPTURE = "CAPTURE", _("Print charge")
        ADJUSTMENT = "ADJUSTMENT", _("Manual adjustment")

    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name="entries")
    kind = models.CharField(_("kind"), max_length=12, choices=Kind.choices)
    amount_kz = models.BigIntegerField(_("amount (Kz)"), help_text=_("Positive = credit."))
    balance_after_kz = models.BigIntegerField(_("balance after (Kz)"))
    job = models.ForeignKey(
        Job, null=True, blank=True, on_delete=models.PROTECT, related_name="ledger_entries"
    )
    # Receita atribuída ao kiosk onde a impressão aconteceu.
    kiosk = models.ForeignKey(
        Kiosk, null=True, blank=True, on_delete=models.PROTECT, related_name="ledger_entries"
    )
    reference = models.CharField(_("reference"), max_length=120, blank=True)
    idempotency_key = models.CharField(max_length=120, unique=True, null=True, blank=True)
    actor = models.CharField(_("actor"), max_length=100)
    memo = models.CharField(_("memo"), max_length=255, blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        verbose_name = _("ledger entry")
        verbose_name_plural = _("ledger entries")
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["kind", "kiosk", "created_at"], name="ledger_revenue_idx")]
        constraints = [
            models.CheckConstraint(
                name="ledger_topup_positive", condition=~Q(kind="TOPUP") | Q(amount_kz__gt=0)
            ),
            models.CheckConstraint(
                name="ledger_capture_negative_with_kiosk",
                condition=~Q(kind="CAPTURE")
                | Q(amount_kz__lt=0, kiosk__isnull=False, job__isnull=False),
            ),
        ]

    def __str__(self):
        return f"{self.kind} {self.amount_kz:+d} Kz"


class WalletHold(models.Model):
    """Valor cativo para um Job, liquidado no fim (débito do impresso + libertação do resto)."""

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", _("Active")
        SETTLED = "SETTLED", _("Settled")

    job = models.OneToOneField(Job, on_delete=models.PROTECT, related_name="wallet_hold")
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name="holds")
    amount_kz = models.PositiveBigIntegerField(_("held (Kz)"))
    captured_kz = models.PositiveBigIntegerField(_("captured (Kz)"), default=0)
    status = models.CharField(
        _("status"), max_length=10, choices=Status.choices, default=Status.ACTIVE
    )
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    settled_at = models.DateTimeField(_("settled at"), null=True, blank=True)

    class Meta:
        verbose_name = _("wallet hold")
        verbose_name_plural = _("wallet holds")
        constraints = [
            models.CheckConstraint(
                name="hold_capture_within_amount", condition=Q(captured_kz__lte=F("amount_kz"))
            ),
        ]

    def __str__(self):
        return f"Hold {self.amount_kz} Kz [{self.status}]"
