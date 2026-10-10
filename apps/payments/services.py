import hashlib
import uuid
from datetime import timedelta

import structlog
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.wallets import services as wallet_services
from domain.payments.mock import MockPaymentProvider
from domain.payments.provider import InvalidWebhook, PaymentProvider, PaymentStatus, PaymentUpdate
from domain.payments.strongpay import StrongPayProvider

from .models import TopUp, WebhookEvent

log = structlog.get_logger(__name__)

DESCRIPTION = "Carregamento carteira"


def get_provider(name: str | None = None) -> PaymentProvider:
    name = name or settings.PAYMENT_PROVIDER
    if name == "strongpay":
        return StrongPayProvider(
            base_url=settings.STRONGPAY_BASE_URL,
            api_key=settings.STRONGPAY_API_KEY,
            webhook_secret=settings.STRONGPAY_WEBHOOK_SECRET,
        )
    if name == "mock":
        return MockPaymentProvider(webhook_secret=settings.MOCK_PAYMENT_SECRET)
    raise ValueError(f"Unknown payment provider: {name}")


class TopUpAmountInvalid(ValueError):
    pass


def create_topup(user, amount_kz: int, *, actor: str, idempotency_key: str | None = None):
    if not settings.TOPUP_MIN_KZ <= amount_kz <= settings.TOPUP_MAX_KZ:
        raise TopUpAmountInvalid(
            f"Amount must be between {settings.TOPUP_MIN_KZ} and {settings.TOPUP_MAX_KZ} Kz"
        )
    wallet = wallet_services.wallet_for(user)
    key = f"{user.pk}:{idempotency_key}" if idempotency_key else str(uuid.uuid4())
    existing = TopUp.objects.filter(idempotency_key=key).first()
    if existing:
        return existing

    provider = get_provider()
    order = provider.create_order(amount_kz=amount_kz, description=DESCRIPTION, merchant_ref=key)
    try:
        topup = TopUp.objects.create(
            wallet=wallet,
            amount_kz=amount_kz,
            provider=provider.name,
            provider_payment_id=order.provider_payment_id,
            transaction_id=order.transaction_id or "",
            entity=order.entity or "",
            reference=order.reference or "",
            expires_at=order.expires_at,
            idempotency_key=key,
            raw_payload=order.raw,
            created_by=actor,
        )
    except IntegrityError:
        return TopUp.objects.get(idempotency_key=key)
    log.info(
        "topup.created",
        topup_id=str(topup.id),
        amount_kz=amount_kz,
        provider=provider.name,
        reference=topup.reference,
    )
    return topup


def apply_update(provider_name: str, update: PaymentUpdate, *, source: str) -> TopUp | None:
    """Aplica um estado vindo do fornecedor. Idempotente; ``paid`` nunca regride.

    Um pagamento que chega depois de a referência expirar localmente continua a
    ser creditado: o dinheiro entrou e pertence ao cliente.
    """
    with transaction.atomic():
        topup = (
            TopUp.objects.select_for_update()
            .filter(provider=provider_name, provider_payment_id=update.provider_payment_id)
            .first()
        )
        if topup is None:
            log.warning("topup.unknown", provider_payment_id=update.provider_payment_id)
            return None
        topup.events = [
            *topup.events,
            {
                "source": source,
                "status": update.status.value,
                "amount_kz": update.amount_kz,
                "at": timezone.now().isoformat(),
            },
        ][-50:]
        fields = ["events", "updated_at"]

        if update.status == PaymentStatus.PAID and topup.status != TopUp.Status.PAID:
            paid = update.amount_kz or topup.amount_kz
            if paid != topup.amount_kz:
                log.warning("topup.amount_mismatch", expected=topup.amount_kz, paid=paid)
            entry = wallet_services.credit(
                topup.wallet,
                paid,
                idempotency_key=f"topup:{topup.id}",
                actor=f"payment:{provider_name}",
                reference=f"{topup.entity}/{topup.reference}",
                memo=f"Top-up {provider_name}",
            )
            topup.status = TopUp.Status.PAID
            topup.paid_amount_kz = paid
            topup.paid_at = update.paid_at or timezone.now()
            topup.ledger_entry = entry
            fields += ["status", "paid_amount_kz", "paid_at", "ledger_entry"]
        elif (
            update.status
            in (
                PaymentStatus.FAILED,
                PaymentStatus.CANCELLED,
                PaymentStatus.EXPIRED,
            )
            and topup.status == TopUp.Status.PENDING
        ):
            topup.status = update.status.value
            fields.append("status")
        topup.save(update_fields=fields)

    log.info("topup.update", topup_id=str(topup.id), status=topup.status, source=source)
    return topup


def handle_webhook(provider_name: str, headers: dict, body: bytes) -> tuple[str, TopUp | None]:
    digest = hashlib.sha256(body).hexdigest()
    event, created = WebhookEvent.objects.get_or_create(
        body_sha256=digest,
        defaults={
            "provider": provider_name,
            "headers": {k: v for k, v in headers.items() if k.lower().startswith("x-")},
            "body": body.decode("utf-8", errors="replace")[:20000],
        },
    )
    if not created and event.valid:
        return "duplicate", event.topup

    try:
        update = get_provider(provider_name).parse_webhook(headers, body)
    except InvalidWebhook as exc:
        event.result = "invalid"
        event.save(update_fields=["result"])
        log.warning("webhook.invalid", provider=provider_name, reason=str(exc))
        raise

    topup = apply_update(provider_name, update, source="webhook")
    event.valid = True
    event.topup = topup
    event.result = topup.status if topup else "unknown_payment"
    event.save(update_fields=["valid", "topup", "result"])
    return event.result, topup


def simulate_payment(topup: TopUp, status: str = "paid") -> TopUp:
    """Só para o fornecedor mock: gera o webhook assinado e passa pelo caminho real."""
    if topup.provider != "mock" or not settings.PAYMENTS_ALLOW_SIMULATION:
        raise PermissionError("Simulation is only available for mock payments")
    provider = get_provider("mock")
    headers, body = provider.build_webhook(topup.provider_payment_id, topup.amount_kz, status)
    handle_webhook("mock", headers, body)
    topup.refresh_from_db()
    return topup


def reconcile_pending(max_age_hours: int = 48) -> int:
    """Consulta o fornecedor para carregamentos pendentes (rede de segurança do webhook)."""
    now = timezone.now()
    pending = TopUp.objects.filter(
        status=TopUp.Status.PENDING,
        created_at__lt=now - timedelta(minutes=1),
        created_at__gt=now - timedelta(hours=max_age_hours),
    )
    changed = 0
    for topup in pending[:200]:
        provider = get_provider(topup.provider)
        update = provider.fetch(topup.provider_payment_id)
        if update.status != PaymentStatus.PENDING:
            apply_update(topup.provider, update, source="reconcile")
            changed += 1
        elif topup.expires_at and topup.expires_at < now - timedelta(hours=1):
            TopUp.objects.filter(pk=topup.pk, status=TopUp.Status.PENDING).update(
                status=TopUp.Status.EXPIRED, updated_at=now
            )
            changed += 1
    return changed
