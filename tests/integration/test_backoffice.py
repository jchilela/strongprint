from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.jobs.models import Job
from apps.monitoring.models import Alert, Downtime
from apps.payments.models import TopUp
from apps.wallets import services as wallet
from common.auth import kiosk_activity, mark_kiosk_active
from domain.jobs.state_machine import JobState
from tests.factories import JobFactory, KioskFactory, PrinterFactory, UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def business():
    kiosk = KioskFactory()
    PrinterFactory(
        kiosk=kiosk,
        status="WARNING",
        status_detail={
            "toner": {"black cartridge": 8, "cyan cartridge": 60},
            "reasons": ["toner-low-warning"],
        },
    )
    customer = UserFactory(full_name="Ana Cliente", phone="+244923456789")
    w = wallet.wallet_for(customer)
    wallet.credit(w, 5000, idempotency_key="seed", actor="t")
    TopUp.objects.create(
        wallet=w,
        amount_kz=5000,
        paid_amount_kz=5000,
        status="paid",
        provider="mock",
        provider_payment_id="p1",
        idempotency_key="k1",
        paid_at=timezone.now(),
    )
    job = JobFactory(kiosk=kiosk, customer=customer, total_kz=700)
    wallet.place_hold(job, actor="t")
    wallet.settle_hold(job, 700, actor="t")
    Job.objects.filter(pk=job.pk).update(
        state=JobState.COMPLETED, pages_printed=7, finished_at=timezone.now()
    )
    Alert.objects.create(kind="toner_low", severity="warning", kiosk=kiosk, message="toner baixo")
    return kiosk, customer


@pytest.fixture
def owner(client):
    user = User.objects.create_superuser("911111111", password="admin-pass-123")
    client.force_login(user)
    return client


def test_dashboard_shows_business(owner, business):
    kiosk, customer = business
    mark_kiosk_active(kiosk.pk, customer.pk)
    Downtime.objects.create(
        kiosk=kiosk,
        reason="paper_out",
        detail={"reasons": ["media-empty-error"]},
        started_at=timezone.now() - timedelta(hours=7),
        ended_at=timezone.now() - timedelta(hours=1, minutes=24),
    )
    response = owner.get(reverse("strongprint-dashboard"))
    assert response.status_code == 200
    ctx = response.context
    assert b"media-empty-error" in response.content
    assert ctx["downtimes"][0].duration_text == "5 h 36 min"
    assert ctx["sales"]["today"]["revenue_kz"] == 700 and ctx["sales"]["all"]["faces"] == 7
    assert ctx["sales"]["month"]["topups_kz"] == 5000
    assert ctx["wallets"]["balance_kz"] == 4300 and ctx["wallets"]["with_balance"] == 1
    row = next(r for r in ctx["kiosks"] if r["kiosk"] == kiosk)
    assert (
        row["in_use"] and row["toner_min"] == 8 and row["open_alerts"] == 1 and row["today"] == 700
    )
    assert row["availability"]["pct"] == round(100 * (1 - 5.6 / 168), 1)
    partner = next(p for p in ctx["partners"] if p["partner"] == kiosk.site.partner)
    assert partner["today"] == 700 and partner["kiosks"] == 1
    assert ctx["daily"][-1]["kz"] == 700
    html = response.content.decode()
    assert kiosk.code in html and "toner baixo" in html


def test_customer_search_links_to_add_credit(owner, business):
    _, customer = business
    response = owner.get(reverse("strongprint-dashboard"), {"q": "923456"})
    assert customer in response.context["customers"]
    assert (
        reverse("admin:wallets_wallet_adjust", args=[customer.wallet.pk])
        in response.content.decode()
    )
    by_name = owner.get(reverse("strongprint-dashboard"), {"q": "Ana Cli"})
    assert customer in by_name.context["customers"]
    assert owner.get(reverse("strongprint-dashboard"), {"q": "zz"}).context["customers"] == []


def test_staff_without_superuser_sees_model_list(client):
    staff = UserFactory(is_staff=True)
    client.force_login(staff)
    response = client.get(reverse("strongprint-dashboard"))
    assert response.status_code == 200 and "sales" not in response.context


def test_models_index_and_anonymous(client, owner):
    assert owner.get(reverse("admin-models")).status_code == 200
    client.logout()
    assert "/admin/login/" in client.get(reverse("strongprint-dashboard")).url


def test_kiosk_activity_cache(business):
    kiosk, customer = business
    assert kiosk_activity([kiosk.pk]) == {}
    mark_kiosk_active(kiosk.pk, customer.pk)
    assert kiosk_activity([kiosk.pk])[kiosk.pk]["user_id"] == customer.pk
