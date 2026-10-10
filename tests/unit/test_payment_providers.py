import json

import httpx
import pytest
import respx

from domain.payments.mock import MockPaymentProvider
from domain.payments.provider import (
    InvalidWebhook,
    PaymentProviderError,
    PaymentStatus,
    parse_amount_kz,
    parse_datetime,
    parse_status,
)
from domain.payments.strongpay import StrongPayProvider, sign

BASE = "http://strongpay.test"
CREATED = {
    "id": "7b6e4b8f-b7f4-4a8b-97f1-4d8a9e58a123",
    "transaction_id": "c27f4717-b7a6-4e44-b1e3-ffd39f90e802",
    "method": "reference",
    "status": "pending",
    "amount": "2500.00",
    "currency": "AOA",
    "payment_reference": "360886123",
    "entity_number": "11466",
    "expires_at": "2026-05-12T21:13:50.466231+00:00",
}


def provider(**kwargs):
    defaults = {"base_url": BASE, "api_key": "sp_" + "a" * 64, "webhook_secret": "whsec"}
    return StrongPayProvider(**{**defaults, **kwargs})


@respx.mock
def test_create_order_sends_ref_charge():
    route = respx.post(f"{BASE}/api/v1/payments").mock(
        return_value=httpx.Response(201, json=CREATED)
    )
    order = provider().create_order(
        amount_kz=2500, description="Carregamento carteira", merchant_ref="k"
    )
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer sp_" + "a" * 64
    assert json.loads(request.content) == {
        "method": "ref",
        "amount": 2500.0,
        "currency": "AOA",
        "description": "Carregamento carteira",
        "product": "wallet",
    }
    assert (order.provider_payment_id, order.entity, order.reference) == (
        CREATED["id"],
        "11466",
        "360886123",
    )
    assert order.status == PaymentStatus.PENDING
    assert order.expires_at.year == 2026


@respx.mock
@pytest.mark.parametrize(
    "response,retryable",
    [
        (httpx.Response(503), True),
        (httpx.Response(422, json={"detail": "bad"}), False),
        (httpx.Response(201, text="not json"), True),
        (httpx.Response(201, json={"status": "pending"}), False),
    ],
)
def test_create_order_errors(response, retryable):
    respx.post(f"{BASE}/api/v1/payments").mock(return_value=response)
    with pytest.raises(PaymentProviderError) as exc:
        provider().create_order(amount_kz=100, description="x", merchant_ref="k")
    assert exc.value.retryable is retryable


@respx.mock
def test_network_error_is_retryable():
    respx.post(f"{BASE}/api/v1/payments").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(PaymentProviderError) as exc:
        provider().create_order(amount_kz=100, description="x", merchant_ref="k")
    assert exc.value.retryable


def test_missing_api_key():
    with pytest.raises(PaymentProviderError):
        provider(api_key="").create_order(amount_kz=100, description="x", merchant_ref="k")


@respx.mock
def test_fetch():
    respx.get(f"{BASE}/api/v1/payments/abc").mock(
        return_value=httpx.Response(200, json={"id": "x", "status": "paid", "amount": 39900.0})
    )
    update = provider().fetch("abc")
    assert (update.provider_payment_id, update.status, update.amount_kz) == (
        "abc",
        PaymentStatus.PAID,
        39900,
    )


def webhook_body(**overrides):
    data = {
        "event": "payment.updated",
        "payment_id": CREATED["id"],
        "status": "paid",
        "amount": "2500.00",
        "paid_at": "2026-05-07T21:00:00+00:00",
    }
    return json.dumps({**data, **overrides}).encode()


def test_webhook_valid_signature():
    body = webhook_body()
    update = provider().parse_webhook({"X-StrongPay-Signature": sign("whsec", body)}, body)
    assert (update.status, update.amount_kz) == (PaymentStatus.PAID, 2500)
    assert update.paid_at.isoformat() == "2026-05-07T21:00:00+00:00"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-StrongPay-Signature": "sha256=deadbeef"},
        {"X-StrongPay-Signature": sign("other", b"x")},
    ],
)
def test_webhook_rejects_bad_signature(headers):
    with pytest.raises(InvalidWebhook):
        provider().parse_webhook(headers, webhook_body())


def test_webhook_requires_configured_secret():
    body = webhook_body()
    with pytest.raises(InvalidWebhook):
        provider(webhook_secret="").parse_webhook({"X-StrongPay-Signature": sign("", body)}, body)


@pytest.mark.parametrize("body", [b"not json", json.dumps({"status": "paid"}).encode()])
def test_webhook_rejects_malformed_body(body):
    with pytest.raises(InvalidWebhook):
        provider().parse_webhook({"X-StrongPay-Signature": sign("whsec", body)}, body)


def test_mock_provider_roundtrip():
    mock = MockPaymentProvider(webhook_secret="s")
    order = mock.create_order(amount_kz=1000, description="x", merchant_ref="k")
    assert order.entity == "00000" and len(order.reference) == 9
    headers, body = mock.build_webhook(order.provider_payment_id, 1000, "paid")
    update = mock.parse_webhook(headers, body)
    assert (update.provider_payment_id, update.status, update.amount_kz) == (
        order.provider_payment_id,
        PaymentStatus.PAID,
        1000,
    )
    assert mock.fetch("x").status == PaymentStatus.PENDING
    with pytest.raises(NotImplementedError):
        mock.refund("x", 10)


def test_parsers():
    assert parse_status("PAID") == PaymentStatus.PAID
    assert parse_status("weird") == PaymentStatus.PENDING
    assert parse_amount_kz("2500.40") == 2500
    assert parse_amount_kz(None) is None and parse_amount_kz("x") is None
    assert parse_datetime("bad") is None and parse_datetime(None) is None
    assert parse_datetime("2026-01-01T00:00:00Z").tzinfo is not None
