import re
from datetime import timedelta

import pytest
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.jobs.models import Job
from apps.monitoring.models import Alert
from apps.partners import reports
from apps.partners.models import Kiosk, PartnerMembership
from apps.wallets import services as wallet
from domain.jobs.state_machine import JobState
from tests.factories import JobFactory, KioskFactory, PartnerFactory, SiteFactory, UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def partner_setup():
    partner = PartnerFactory(name="Imetro")
    site = SiteFactory(partner=partner)
    k1, k2 = KioskFactory(site=site), KioskFactory(site=site)
    other = KioskFactory()  # de outro parceiro
    owner = UserFactory(phone="+244923000001", full_name="Dono")
    viewer = UserFactory(phone="+244923000002", full_name="Consulta")
    PartnerMembership.objects.create(partner=partner, user=owner, role="OWNER")
    PartnerMembership.objects.create(partner=partner, user=viewer, role="VIEWER")
    customer = UserFactory(phone="+244923456789")
    w = wallet.wallet_for(customer)
    wallet.credit(w, 10_000, idempotency_key="seed", actor="t")
    for kiosk, amount, faces in [(k1, 500, 5), (k1, 300, 3), (k2, 200, 2), (other, 900, 9)]:
        job = JobFactory(kiosk=kiosk, customer=customer, total_kz=amount)
        wallet.place_hold(job, actor="t")
        wallet.settle_hold(job, amount, actor="t")
        Job.objects.filter(pk=job.pk).update(
            state=JobState.COMPLETED, pages_printed=faces, finished_at=timezone.now()
        )
    return {
        "partner": partner,
        "k1": k1,
        "k2": k2,
        "other": other,
        "owner": owner,
        "viewer": viewer,
    }


def login(client, user):
    client.force_login(user)
    return client


def test_login_flow_only_for_members(client, partner_setup):
    response = client.post(reverse("partner:login"), {"phone": "923456789"})  # cliente comum
    assert response.status_code == 200 and "_auth_user_id" not in client.session
    response = client.post(reverse("partner:login"), {"phone": "923000001"})
    assert response.url == reverse("partner:login_code")
    text = " ".join(str(m) for m in get_messages(response.wsgi_request))
    code = re.search(r"(\d{6})", text).group(1)
    response = client.post(reverse("partner:login_code"), {"code": code})
    assert response.url == reverse("partner:dashboard")
    assert client.get(reverse("partner:dashboard")).status_code == 200


def test_pages_require_partner_access(client, partner_setup):
    assert "/parceiro/entrar/" in client.get(reverse("partner:dashboard")).url
    login(client, UserFactory())
    response = client.get(reverse("partner:dashboard"))
    assert response.url == reverse("partner:login")


def test_dashboard_scoped_revenue(client, partner_setup):
    login(client, partner_setup["owner"])
    response = client.get(reverse("partner:dashboard"))
    ctx = response.context
    assert ctx["totals"] == {"revenue_kz": 1000, "jobs": 3, "faces": 10}
    codes = {r["kiosk"].code: r["revenue_kz"] for r in ctx["rows"]}
    assert codes == {partner_setup["k1"].code: 800, partner_setup["k2"].code: 200}
    assert partner_setup["other"].code not in response.content.decode()
    assert ctx["daily"][-1]["kz"] == 1000 and ctx["daily"][-1]["pct"] == 100
    for key in ("today", "30d", "month"):
        assert client.get(reverse("partner:dashboard"), {"periodo": key}).status_code == 200


def test_other_partner_kiosk_is_404(client, partner_setup):
    login(client, partner_setup["owner"])
    url = reverse("partner:kiosk", args=[partner_setup["other"].code])
    assert client.get(url).status_code == 404


def test_owner_edits_kiosk_and_maintenance(client, partner_setup):
    k1 = partner_setup["k1"]
    login(client, partner_setup["owner"])
    url = reverse("partner:kiosk", args=[k1.code])
    page = client.get(url)
    assert page.status_code == 200 and page.context["totals"]["revenue_kz"] == 800
    client.post(url, {"name": "Biblioteca", "location_hint": "Piso 1"})
    k1.refresh_from_db()
    assert (k1.name, k1.location_hint) == ("Biblioteca", "Piso 1")

    status_url = reverse("partner:kiosk_status", args=[k1.code])
    client.post(status_url, {"action": "maintenance"})
    k1.refresh_from_db()
    assert k1.status == Kiosk.Status.MAINTENANCE
    client.post(status_url, {"action": "maintenance"})  # já está: recusado
    client.post(status_url, {"action": "resume"})
    k1.refresh_from_db()
    assert k1.status == Kiosk.Status.OFFLINE


def test_viewer_cannot_change(client, partner_setup):
    k1 = partner_setup["k1"]
    login(client, partner_setup["viewer"])
    client.post(reverse("partner:kiosk", args=[k1.code]), {"name": "X", "location_hint": ""})
    client.post(reverse("partner:kiosk_status", args=[k1.code]), {"action": "maintenance"})
    k1.refresh_from_db()
    assert k1.name != "X" and k1.status != Kiosk.Status.MAINTENANCE


def test_jobs_list_masks_customers(client, partner_setup):
    login(client, partner_setup["owner"])
    html = client.get(reverse("partner:jobs")).content.decode()
    assert "923456789" not in html and "+244 9•• ••• 789" in html
    filtered = client.get(reverse("partner:jobs"), {"kiosk": partner_setup["k2"].code})
    assert len(filtered.context["jobs"]) == 1
    by_state = client.get(reverse("partner:jobs"), {"estado": "FAILED"})
    assert by_state.context["jobs"] == []


def test_alerts_page(client, partner_setup):
    Alert.objects.create(
        kind="paper_out", severity="critical", kiosk=partner_setup["k1"], message="sem papel"
    )
    Alert.objects.create(
        kind="toner_low", severity="warning", kiosk=partner_setup["other"], message="outro"
    )
    login(client, partner_setup["owner"])
    html = client.get(reverse("partner:alerts")).content.decode()
    assert "sem papel" in html and "outro" not in html


def test_superuser_sees_all_partners_and_switches(client, partner_setup):
    second = PartnerFactory(name="UAN")
    admin = User.objects.create_superuser("911111111", password="x-pass-123")
    login(client, admin)
    page = client.get(reverse("partner:dashboard"))
    assert (
        second in page.context["partners"] and partner_setup["partner"] in page.context["partners"]
    )
    client.post(reverse("partner:switch"), {"partner": second.pk})
    assert client.get(reverse("partner:dashboard")).context["partner"] == second


def test_period_parsing():
    today = timezone.localdate()
    assert reports.period_from("today").start == today
    assert reports.period_from("bogus").key == "7d"
    custom = reports.period_from(None, (today - timedelta(days=3)).isoformat(), today.isoformat())
    assert custom.key == "custom" and len(custom.days) == 4
    assert reports.period_from(None, "x", "y").key == "7d"
    assert reports.mask_phone("") == "—"


def test_admin_creates_user_without_password(client):
    admin = User.objects.create_superuser("911111111", password="x-pass-123")
    client.force_login(admin)
    response = client.post(
        reverse("admin:accounts_user_add"),
        {"phone": "923 777 888", "full_name": "Gestor Imetro", "password1": "", "password2": ""},
    )
    assert response.status_code == 302, response.content
    user = User.objects.get(phone="+244923777888")
    assert not user.has_usable_password()
    response = client.post(
        reverse("admin:accounts_user_add"),
        {"phone": "923777889", "full_name": "X", "password1": "a-Strong-pass-9", "password2": "b"},
    )
    assert response.status_code == 200
