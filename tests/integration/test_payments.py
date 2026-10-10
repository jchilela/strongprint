"""Carregamentos da carteira: criação, webhook (idempotente), simulação e reconciliação."""

import json
from datetime import timedelta

import httpx
import pytest
import respx
from django.utils import timezone

from apps.payments import services, tasks
from apps.payments.models import TopUp, WebhookEvent
from apps.wallets.models import LedgerEntry
from domain.payments.mock import MockPaymentProvider
from domain.payments.strongpay import sign

pytestmark = pytest.mark.django_db


def mock_webhook(topup, status="paid", amount=None):
    provider = MockPaymentProvider(webhook_secret="test-mock-secret")
    return provider.build_webhook(topup.provider_payment_id, amount or topup.amount_kz, status)


def post_webhook(client, provider, headers, body):
    extra = {f"HTTP_{k.upper().replace('-', '_')}": v for k, v in headers.items()}
    return client.post(
        f"/api/v1/payments/webhook/{provider}", body, content_type="application/json", **extra
    )


def test_create_topup_returns_reference(api):
    response = api.post("/api/v1/wallet/topups", {"amount_kz": 2000}, format="json")
    assert response.status_code == 201, response.data
    assert response.data["status"] == "pending"
    assert response.data["entity"] == "00000" and len(response.data["reference"]) == 9
    assert api.get("/api/v1/wallet").data["pending_topups"][0]["id"] == response.data["id"]


@pytest.mark.parametrize("amount", [10, 10_000_000])
def test_topup_amount_limits(api, amount):
    assert (
        api.post("/api/v1/wallet/topups", {"amount_kz": amount}, format="json").status_code == 400
    )


def test_topup_idempotency_key(api):
    headers = {"HTTP_IDEMPOTENCY_KEY": "abc"}
    first = api.post("/api/v1/wallet/topups", {"amount_kz": 500}, format="json", **headers)
    second = api.post("/api/v1/wallet/topups", {"amount_kz": 500}, format="json", **headers)
    assert first.data["id"] == second.data["id"]
    assert TopUp.objects.count() == 1


def test_webhook_credits_wallet_once(client, customer):
    topup = services.create_topup(customer, 1500, actor="test")
    headers, body = mock_webhook(topup)
    for _ in range(3):  # o StrongPay repete entregas
        response = post_webhook(client, "mock", headers, body)
        assert response.status_code == 200
    topup.refresh_from_db()
    assert topup.status == TopUp.Status.PAID
    assert topup.wallet.balance_kz == 1500
    assert LedgerEntry.objects.filter(kind=LedgerEntry.Kind.TOPUP).count() == 1
    assert WebhookEvent.objects.count() == 1

    # paid nunca regride
    headers, body = mock_webhook(topup, status="failed")
    post_webhook(client, "mock", headers, body)
    topup.refresh_from_db()
    assert topup.status == TopUp.Status.PAID


def test_webhook_invalid_signature(client, customer):
    topup = services.create_topup(customer, 1500, actor="test")
    _, body = mock_webhook(topup)
    response = post_webhook(client, "mock", {"X-StrongPay-Signature": "sha256=bad"}, body)
    assert response.status_code == 400
    topup.refresh_from_db()
    assert topup.status == TopUp.Status.PENDING
    assert WebhookEvent.objects.get().result == "invalid"
    assert post_webhook(client, "nope", {}, body).status_code == 404


def test_webhook_unknown_payment_is_acknowledged(client):
    provider = MockPaymentProvider(webhook_secret="test-mock-secret")
    headers, body = provider.build_webhook("does-not-exist", 100)
    response = post_webhook(client, "mock", headers, body)
    assert response.status_code == 200
    assert response.json()["result"] == "unknown_payment"


def test_late_payment_after_expiry_is_still_credited(client, customer):
    topup = services.create_topup(customer, 700, actor="test")
    TopUp.objects.filter(pk=topup.pk).update(status=TopUp.Status.EXPIRED)
    headers, body = mock_webhook(topup)
    post_webhook(client, "mock", headers, body)
    topup.refresh_from_db()
    assert topup.status == TopUp.Status.PAID
    assert topup.wallet.balance_kz == 700


def test_failed_status(client, customer):
    topup = services.create_topup(customer, 700, actor="test")
    headers, body = mock_webhook(topup, status="cancelled")
    post_webhook(client, "mock", headers, body)
    topup.refresh_from_db()
    assert topup.status == TopUp.Status.CANCELLED
    assert topup.wallet.balance_kz == 0


def test_amount_mismatch_credits_paid_amount(client, customer):
    topup = services.create_topup(customer, 1000, actor="test")
    headers, body = mock_webhook(topup, amount=800)
    post_webhook(client, "mock", headers, body)
    topup.refresh_from_db()
    assert (topup.paid_amount_kz, topup.wallet.balance_kz) == (800, 800)


def test_simulate_endpoint(api, settings):
    created = api.post("/api/v1/wallet/topups", {"amount_kz": 900}, format="json").data
    response = api.post(f"/api/v1/wallet/topups/{created['id']}/simulate", {}, format="json")
    assert response.data["status"] == "paid"
    assert api.get("/api/v1/wallet").data["available_kz"] == 900
    bad = api.post(
        f"/api/v1/wallet/topups/{created['id']}/simulate", {"status": "x"}, format="json"
    )
    assert bad.status_code == 400

    settings.PAYMENTS_ALLOW_SIMULATION = False
    other = api.post("/api/v1/wallet/topups", {"amount_kz": 900}, format="json").data
    blocked = api.post(f"/api/v1/wallet/topups/{other['id']}/simulate", {}, format="json")
    assert blocked.status_code == 403


STRONGPAY = "http://strongpay.test"


@pytest.fixture
def strongpay(settings):
    settings.PAYMENT_PROVIDER = "strongpay"
    settings.STRONGPAY_BASE_URL = STRONGPAY
    settings.STRONGPAY_API_KEY = "sp_test"
    settings.STRONGPAY_WEBHOOK_SECRET = "whsec"
    with respx.mock(base_url=STRONGPAY) as router:
        yield router


def test_strongpay_end_to_end(strongpay, api, client, customer):
    strongpay.post("/api/v1/payments").mock(
        return_value=httpx.Response(
            201,
            json={
                "id": "sp-123",
                "transaction_id": "tx-1",
                "status": "pending",
                "payment_reference": "360886123",
                "entity_number": "11466",
                "expires_at": "2026-10-10T21:00:00+00:00",
            },
        )
    )
    created = api.post("/api/v1/wallet/topups", {"amount_kz": 2500}, format="json")
    assert created.status_code == 201, created.data
    assert (created.data["entity"], created.data["reference"]) == ("11466", "360886123")

    body = json.dumps(
        {"event": "payment.updated", "payment_id": "sp-123", "status": "paid", "amount": "2500.00"}
    ).encode()
    response = post_webhook(
        client, "strongpay", {"X-StrongPay-Signature": sign("whsec", body)}, body
    )
    assert response.status_code == 200
    assert api.get("/api/v1/wallet").data["balance_kz"] == 2500


def test_strongpay_provider_error_maps_to_502(strongpay, api):
    strongpay.post("/api/v1/payments").mock(return_value=httpx.Response(422, json={"detail": "x"}))
    response = api.post("/api/v1/wallet/topups", {"amount_kz": 2500}, format="json")
    assert (response.status_code, response.data["error"]) == (502, "payment_provider_error")


def test_reconcile_pending(strongpay, customer):
    strongpay.post("/api/v1/payments").mock(
        side_effect=[
            httpx.Response(201, json={"id": "sp-paid", "status": "pending"}),
            httpx.Response(201, json={"id": "sp-old", "status": "pending"}),
        ]
    )
    paid = services.create_topup(customer, 1000, actor="t")
    old = services.create_topup(customer, 300, actor="t")
    TopUp.objects.update(created_at=timezone.now() - timedelta(minutes=5))
    TopUp.objects.filter(pk=old.pk).update(expires_at=timezone.now() - timedelta(hours=2))
    strongpay.get("/api/v1/payments/sp-paid").mock(
        return_value=httpx.Response(200, json={"status": "paid", "amount": 1000})
    )
    strongpay.get("/api/v1/payments/sp-old").mock(
        return_value=httpx.Response(200, json={"status": "pending"})
    )
    assert tasks.reconcile_topups() == 2
    paid.refresh_from_db()
    old.refresh_from_db()
    assert (paid.status, old.status) == (TopUp.Status.PAID, TopUp.Status.EXPIRED)
    assert paid.wallet.balance_kz == 1000


def test_reconcile_survives_provider_outage(strongpay, customer):
    strongpay.post("/api/v1/payments").mock(
        return_value=httpx.Response(201, json={"id": "sp-x", "status": "pending"})
    )
    services.create_topup(customer, 1000, actor="t")
    TopUp.objects.update(created_at=timezone.now() - timedelta(minutes=5))
    strongpay.get("/api/v1/payments/sp-x").mock(return_value=httpx.Response(503))
    assert tasks.reconcile_topups() == 0
