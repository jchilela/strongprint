"""StrongPay (http://strongpay.strongbox.ao) — cobranças por referência via AppyPay.

- Criar: ``POST /api/v1/payments`` com ``Authorization: Bearer <SYSTEM_API_KEY>``.
- Estado: webhook ``payment.updated`` assinado com HMAC-SHA256 (``X-StrongPay-Signature``)
  e, como rede de segurança, ``GET /api/v1/payments/{id}``.
"""

import hashlib
import hmac
import json
from collections.abc import Mapping

import httpx

from .provider import (
    InvalidWebhook,
    Order,
    PaymentProvider,
    PaymentProviderError,
    PaymentUpdate,
    parse_amount_kz,
    parse_datetime,
    parse_status,
)

SIGNATURE_HEADER = "x-strongpay-signature"


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class StrongPayProvider(PaymentProvider):
    name = "strongpay"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        webhook_secret: str,
        product: str = "wallet",
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.webhook_secret = webhook_secret
        self.product = product
        self.timeout = timeout
        self.transport = transport

    def _client(self) -> httpx.Client:
        if not self.api_key:
            raise PaymentProviderError("STRONGPAY_API_KEY is not configured")
        return httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            transport=self.transport,
            headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
        )

    def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            with self._client() as client:
                response = client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise PaymentProviderError(f"StrongPay unreachable: {exc}", retryable=True) from exc
        if response.status_code >= 500:
            raise PaymentProviderError(
                f"StrongPay error {response.status_code}",
                retryable=True,
                status_code=response.status_code,
            )
        if response.status_code >= 400:
            raise PaymentProviderError(
                f"StrongPay rejected the request ({response.status_code}): {response.text[:300]}",
                status_code=response.status_code,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise PaymentProviderError("StrongPay returned invalid JSON", retryable=True) from exc

    def create_order(self, *, amount_kz: int, description: str, merchant_ref: str) -> Order:
        data = self._request(
            "POST",
            "/api/v1/payments",
            json={
                "method": "ref",
                "amount": float(amount_kz),
                "currency": "AOA",
                "description": description,
                "product": self.product,
            },
        )
        if not data.get("id"):
            raise PaymentProviderError(f"StrongPay response without id: {data}")
        return Order(
            provider_payment_id=str(data["id"]),
            status=parse_status(data.get("status")),
            entity=data.get("entity_number"),
            reference=data.get("payment_reference"),
            expires_at=parse_datetime(data.get("expires_at")),
            transaction_id=data.get("transaction_id"),
            raw=data,
        )

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> PaymentUpdate:
        if not self.webhook_secret:
            raise InvalidWebhook("STRONGPAY_WEBHOOK_SECRET is not configured")
        lowered = {k.lower(): v for k, v in headers.items()}
        signature = lowered.get(SIGNATURE_HEADER, "")
        if not hmac.compare_digest(sign(self.webhook_secret, body), signature):
            raise InvalidWebhook("Invalid signature")
        try:
            data = json.loads(body)
        except ValueError as exc:
            raise InvalidWebhook("Body is not JSON") from exc
        if not data.get("payment_id"):
            raise InvalidWebhook("Missing payment_id")
        return PaymentUpdate(
            provider_payment_id=str(data["payment_id"]),
            status=parse_status(data.get("status")),
            amount_kz=parse_amount_kz(data.get("amount")),
            paid_at=parse_datetime(data.get("paid_at")),
            raw=data,
        )

    def fetch(self, provider_payment_id: str) -> PaymentUpdate:
        data = self._request("GET", f"/api/v1/payments/{provider_payment_id}")
        return PaymentUpdate(
            provider_payment_id=provider_payment_id,
            status=parse_status(data.get("status")),
            amount_kz=parse_amount_kz(data.get("amount")),
            raw=data,
        )
