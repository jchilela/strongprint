"""Operações da carteira. Todas bloqueiam a carteira (SELECT ... FOR UPDATE)
antes de tocar em saldos, por esta ordem: carteira → cativação."""

import structlog
from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone

from .models import LedgerEntry, Wallet, WalletHold

log = structlog.get_logger(__name__)


class InsufficientFunds(Exception):
    def __init__(self, available_kz: int, required_kz: int):
        self.available_kz = available_kz
        self.required_kz = required_kz
        super().__init__(f"Insufficient funds: available {available_kz} Kz, need {required_kz} Kz")


def wallet_for(user) -> Wallet:
    wallet, _ = Wallet.objects.get_or_create(user=user)
    return wallet


def _lock(wallet_id) -> Wallet:
    return Wallet.objects.select_for_update().get(pk=wallet_id)


def credit(
    wallet: Wallet,
    amount_kz: int,
    *,
    idempotency_key: str,
    actor: str,
    kind: str = LedgerEntry.Kind.TOPUP,
    reference: str = "",
    memo: str = "",
) -> LedgerEntry:
    """Credita a carteira. Repetir com a mesma ``idempotency_key`` não duplica."""
    if amount_kz <= 0:
        raise ValueError("Credit amount must be positive")
    with transaction.atomic():
        w = _lock(wallet.pk)
        existing = LedgerEntry.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing
        w.balance_kz += amount_kz
        w.save(update_fields=["balance_kz", "updated_at"])
        entry = LedgerEntry.objects.create(
            wallet=w,
            kind=kind,
            amount_kz=amount_kz,
            balance_after_kz=w.balance_kz,
            reference=reference,
            idempotency_key=idempotency_key,
            actor=actor,
            memo=memo,
        )
    log.info("wallet.credit", wallet_id=w.pk, amount_kz=amount_kz, kind=kind, actor=actor)
    return entry


def adjust(wallet: Wallet, amount_kz: int, *, actor: str, memo: str) -> LedgerEntry:
    """Ajuste manual (positivo ou negativo) feito pela equipa StrongPrint."""
    if amount_kz == 0:
        raise ValueError("Adjustment cannot be zero")
    with transaction.atomic():
        w = _lock(wallet.pk)
        if amount_kz < 0 and w.available_kz < -amount_kz:
            raise InsufficientFunds(w.available_kz, -amount_kz)
        w.balance_kz += amount_kz
        w.save(update_fields=["balance_kz", "updated_at"])
        entry = LedgerEntry.objects.create(
            wallet=w,
            kind=LedgerEntry.Kind.ADJUSTMENT,
            amount_kz=amount_kz,
            balance_after_kz=w.balance_kz,
            actor=actor,
            memo=memo,
        )
    log.info("wallet.adjust", wallet_id=w.pk, amount_kz=amount_kz, actor=actor, memo=memo)
    return entry


def place_hold(job, *, actor: str) -> WalletHold:
    """Cativa ``job.total_kz`` na carteira do cliente. Idempotente por job."""
    amount = job.total_kz or 0
    if amount <= 0:
        raise ValueError("Job has no price to hold")
    wallet = wallet_for(job.customer)
    with transaction.atomic():
        w = _lock(wallet.pk)
        existing = WalletHold.objects.filter(job=job).first()
        if existing:
            return existing
        if w.available_kz < amount:
            raise InsufficientFunds(w.available_kz, amount)
        w.held_kz += amount
        w.save(update_fields=["held_kz", "updated_at"])
        hold = WalletHold.objects.create(job=job, wallet=w, amount_kz=amount)
    log.info("wallet.hold", job_id=str(job.id), wallet_id=w.pk, amount_kz=amount, actor=actor)
    return hold


def settle_hold(job, capture_kz: int, *, actor: str) -> WalletHold:
    """Debita ``capture_kz`` (limitado ao valor cativo) e liberta o resto.

    Idempotente: uma cativação já liquidada é devolvida sem alterações.
    """
    hold = WalletHold.objects.get(job=job)
    with transaction.atomic():
        w = _lock(hold.wallet_id)
        hold = WalletHold.objects.select_for_update().get(pk=hold.pk)
        if hold.status == WalletHold.Status.SETTLED:
            return hold

        capture = max(0, min(int(capture_kz), hold.amount_kz))
        w.held_kz -= hold.amount_kz
        w.balance_kz -= capture
        w.save(update_fields=["held_kz", "balance_kz", "updated_at"])

        if capture:
            LedgerEntry.objects.create(
                wallet=w,
                kind=LedgerEntry.Kind.CAPTURE,
                amount_kz=-capture,
                balance_after_kz=w.balance_kz,
                job=job,
                kiosk_id=job.kiosk_id,
                idempotency_key=f"capture:{job.id}",
                actor=actor,
            )

        hold.status = WalletHold.Status.SETTLED
        hold.captured_kz = capture
        hold.settled_at = timezone.now()
        hold.save(update_fields=["status", "captured_kz", "settled_at"])

    log.info(
        "wallet.settle",
        job_id=str(job.id),
        wallet_id=w.pk,
        held_kz=hold.amount_kz,
        captured_kz=capture,
        released_kz=hold.amount_kz - capture,
        actor=actor,
    )
    return hold


def release_hold(job, *, actor: str) -> WalletHold:
    return settle_hold(job, 0, actor=actor)


def revenue_by_kiosk(entries=None):
    """Receita (Kz) e nº de trabalhos por kiosk, a partir dos débitos de impressão.

    ``entries`` permite filtrar antes (por datas, parceiro, local...).
    """
    qs = entries if entries is not None else LedgerEntry.objects.all()
    return (
        qs.filter(kind=LedgerEntry.Kind.CAPTURE)
        .values("kiosk_id", "kiosk__code", "kiosk__site__name", "kiosk__site__partner__name")
        .annotate(revenue_kz=-Sum("amount_kz"), jobs=Count("job", distinct=True))
        .order_by("kiosk__code")
    )
