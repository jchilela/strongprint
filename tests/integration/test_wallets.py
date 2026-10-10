import threading

import pytest
from django.db import connection
from django.db.models import Sum

from apps.wallets import services
from apps.wallets.models import LedgerEntry, Wallet, WalletHold
from tests.factories import JobFactory, KioskFactory, UserFactory

pytestmark = pytest.mark.django_db


def funded_wallet(amount=1000, user=None):
    wallet = services.wallet_for(user or UserFactory())
    if amount:
        services.credit(wallet, amount, idempotency_key=f"seed:{wallet.pk}", actor="test")
    wallet.refresh_from_db()
    return wallet


def assert_ledger_consistent(wallet):
    wallet.refresh_from_db()
    total = wallet.entries.aggregate(t=Sum("amount_kz"))["t"] or 0
    assert wallet.balance_kz == total


def test_credit_is_idempotent():
    wallet = services.wallet_for(UserFactory())
    first = services.credit(wallet, 500, idempotency_key="proxypay:123", actor="webhook")
    again = services.credit(wallet, 500, idempotency_key="proxypay:123", actor="webhook")
    wallet.refresh_from_db()
    assert first.pk == again.pk
    assert wallet.balance_kz == 500
    assert first.balance_after_kz == 500
    assert_ledger_consistent(wallet)


def test_credit_rejects_non_positive():
    wallet = services.wallet_for(UserFactory())
    with pytest.raises(ValueError):
        services.credit(wallet, 0, idempotency_key="x", actor="t")


def test_hold_reduces_available_not_balance():
    wallet = funded_wallet(1000)
    job = JobFactory(customer=wallet.user, total_kz=600)
    hold = services.place_hold(job, actor="test")
    wallet.refresh_from_db()
    assert hold.amount_kz == 600
    assert (wallet.balance_kz, wallet.held_kz, wallet.available_kz) == (1000, 600, 400)


def test_hold_is_idempotent_per_job():
    wallet = funded_wallet(1000)
    job = JobFactory(customer=wallet.user, total_kz=600)
    services.place_hold(job, actor="test")
    services.place_hold(job, actor="test")
    wallet.refresh_from_db()
    assert wallet.held_kz == 600
    assert WalletHold.objects.count() == 1


def test_hold_insufficient_funds():
    wallet = funded_wallet(300)
    job = JobFactory(customer=wallet.user, total_kz=500)
    with pytest.raises(services.InsufficientFunds) as exc:
        services.place_hold(job, actor="test")
    assert (exc.value.available_kz, exc.value.required_kz) == (300, 500)
    wallet.refresh_from_db()
    assert wallet.held_kz == 0


def test_hold_requires_price():
    job = JobFactory(total_kz=None)
    with pytest.raises(ValueError):
        services.place_hold(job, actor="test")


def test_settle_partial_captures_printed_and_releases_rest():
    wallet = funded_wallet(1000)
    kiosk = KioskFactory()
    job = JobFactory(customer=wallet.user, kiosk=kiosk, total_kz=600)
    services.place_hold(job, actor="test")
    hold = services.settle_hold(job, 400, actor="task:print")
    wallet.refresh_from_db()
    assert (hold.status, hold.captured_kz) == (WalletHold.Status.SETTLED, 400)
    assert (wallet.balance_kz, wallet.held_kz) == (600, 0)
    capture = LedgerEntry.objects.get(kind=LedgerEntry.Kind.CAPTURE)
    assert (capture.amount_kz, capture.kiosk, capture.job) == (-400, kiosk, job)
    assert_ledger_consistent(wallet)


def test_settle_is_idempotent_and_capped():
    wallet = funded_wallet(1000)
    job = JobFactory(customer=wallet.user, total_kz=600)
    services.place_hold(job, actor="test")
    services.settle_hold(job, 9999, actor="test")  # nunca mais do que o cativo
    services.settle_hold(job, 600, actor="test")
    wallet.refresh_from_db()
    assert wallet.balance_kz == 400
    assert LedgerEntry.objects.filter(kind=LedgerEntry.Kind.CAPTURE).count() == 1


def test_release_returns_everything():
    wallet = funded_wallet(1000)
    job = JobFactory(customer=wallet.user, total_kz=600)
    services.place_hold(job, actor="test")
    services.release_hold(job, actor="test")
    wallet.refresh_from_db()
    assert (wallet.balance_kz, wallet.held_kz) == (1000, 0)
    assert not LedgerEntry.objects.filter(kind=LedgerEntry.Kind.CAPTURE).exists()


def test_adjust_credit_and_debit():
    wallet = funded_wallet(500)
    services.adjust(wallet, 200, actor="admin:1", memo="goodwill")
    services.adjust(wallet, -100, actor="admin:1", memo="correction")
    wallet.refresh_from_db()
    assert wallet.balance_kz == 600
    assert_ledger_consistent(wallet)


def test_adjust_cannot_debit_held_funds():
    wallet = funded_wallet(500)
    job = JobFactory(customer=wallet.user, total_kz=400)
    services.place_hold(job, actor="test")
    with pytest.raises(services.InsufficientFunds):
        services.adjust(wallet, -200, actor="admin:1", memo="x")
    with pytest.raises(ValueError):
        services.adjust(wallet, 0, actor="admin:1", memo="x")


def test_revenue_by_kiosk_separates_locations():
    k1, k2 = KioskFactory(), KioskFactory()
    wallet = funded_wallet(5000)
    for kiosk, amount in [(k1, 300), (k1, 200), (k2, 700)]:
        job = JobFactory(customer=wallet.user, kiosk=kiosk, total_kz=amount)
        services.place_hold(job, actor="t")
        services.settle_hold(job, amount, actor="t")
    rows = {r["kiosk__code"]: r for r in services.revenue_by_kiosk()}
    assert rows[k1.code]["revenue_kz"] == 500
    assert rows[k1.code]["jobs"] == 2
    assert rows[k2.code]["revenue_kz"] == 700
    assert rows[k2.code]["kiosk__site__partner__name"] == k2.site.partner.name


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_concurrent_holds_never_overdraw():
    """Duas cativações em simultâneo com saldo só para uma: apenas uma passa."""
    wallet = funded_wallet(500)
    jobs = [JobFactory(customer=wallet.user, total_kz=400) for _ in range(2)]
    barrier = threading.Barrier(2)
    results = []

    def worker(job):
        barrier.wait()
        try:
            services.place_hold(job, actor="race")
            results.append("ok")
        except services.InsufficientFunds:
            results.append("insufficient")
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(j,)) for j in jobs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == ["insufficient", "ok"]
    assert Wallet.objects.get(pk=wallet.pk).held_kz == 400
