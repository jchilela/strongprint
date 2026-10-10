"""Fornecedor simulado: mesmo contrato (e formato de webhook) que o StrongPay.

Serve para desenvolvimento e testes. ``build_webhook`` gera um evento assinado
igual ao que o StrongPay enviaria, para exercitar o caminho real do webhook.
"""

import json
import secrets
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from .provider import Order, PaymentProvider, PaymentStatus, PaymentUpdate
from .strongpay import StrongPayProvider, sign


class MockPaymentProvider(PaymentProvider):
    name = "mock"

    def __init__(self, *, webhook_secret: str = "mock-secret", ttl_minutes: int = 45):
        self.webhook_secret = webhook_secret
        self.ttl_minutes = ttl_minutes
        # Reaproveita a validação de assinatura/normalização do StrongPay.
        self._parser = StrongPayProvider(
            base_url="http://mock", api_key="", webhook_secret=webhook_secret
        )

    def create_order(self, *, amount_kz: int, description: str, merchant_ref: str) -> Order:
        payment_id = str(uuid.uuid4())
        return Order(
            provider_payment_id=payment_id,
            status=PaymentStatus.PENDING,
            entity="00000",
            reference=f"{secrets.randbelow(10**9):09d}",
            expires_at=datetime.now(UTC) + timedelta(minutes=self.ttl_minutes),
            transaction_id=str(uuid.uuid4()),
            raw={"mock": True, "amount": amount_kz, "description": description},
        )

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> PaymentUpdate:
        return self._parser.parse_webhook(headers, body)

    def fetch(self, provider_payment_id: str) -> PaymentUpdate:
        # Sem estado: o mock só muda de estado por webhook simulado.
        return PaymentUpdate(provider_payment_id=provider_payment_id, status=PaymentStatus.PENDING)

    def build_webhook(
        self, provider_payment_id: str, amount_kz: int, status: str = "paid"
    ) -> tuple[dict[str, str], bytes]:
        now = datetime.now(UTC).isoformat()
        body = json.dumps(
            {
                "event": "payment.updated",
                "payment_id": provider_payment_id,
                "status": status,
                "amount": f"{amount_kz:.2f}",
                "currency": "AOA",
                "paid_at": now if status == "paid" else None,
                "updated_at": now,
            }
        ).encode()
        headers = {
            "X-StrongPay-Event": "payment.updated",
            "X-StrongPay-Signature": sign(self.webhook_secret, body),
        }
        return headers, body
