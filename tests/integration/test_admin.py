import pytest
from django.contrib import admin
from django.urls import reverse

from apps.accounts.models import User
from apps.devices.models import EdgeDevice
from apps.wallets import services
from apps.wallets.models import LedgerEntry
from tests.factories import EdgeDeviceFactory, JobFactory, KioskFactory, PrinterFactory, UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def superadmin(client):
    user = User.objects.create_superuser("911111111", password="admin-pass-123")
    client.force_login(user)
    return user


@pytest.fixture
def populated():
    kiosk = KioskFactory()
    EdgeDeviceFactory(printer=PrinterFactory(kiosk=kiosk))
    job = JobFactory(kiosk=kiosk, total_kz=200)
    wallet = services.wallet_for(job.customer)
    services.credit(wallet, 1000, idempotency_key="seed", actor="t")
    services.place_hold(job, actor="t")
    return kiosk


def test_every_admin_changelist_and_add_page_loads(client, superadmin, populated):
    for model, model_admin in admin.site._registry.items():
        meta = model._meta
        url = reverse(f"admin:{meta.app_label}_{meta.model_name}_changelist")
        assert client.get(url).status_code == 200, url
        obj = model.objects.first()
        if obj is not None:
            url = reverse(f"admin:{meta.app_label}_{meta.model_name}_change", args=[obj.pk])
            assert client.get(url).status_code == 200, url
        if model_admin.has_add_permission(_FakeRequest(superadmin)):
            url = reverse(f"admin:{meta.app_label}_{meta.model_name}_add")
            assert client.get(url).status_code == 200, url


class _FakeRequest:
    def __init__(self, user):
        self.user = user


def test_creating_edge_device_shows_token_once(client, superadmin):
    printer = PrinterFactory()
    response = client.post(
        reverse("admin:devices_edgedevice_add"),
        {"printer": printer.pk, "pending_command": "", "is_active": "on"},
        follow=True,
    )
    assert response.status_code == 200
    device = EdgeDevice.objects.get(printer=printer)
    assert device.name == f"{printer.kiosk.code}-PI"
    assert device.kiosk == printer.kiosk
    messages = [str(m) for m in response.context["messages"]]
    token = messages[0].rsplit(": ", 1)[1]
    assert EdgeDevice.authenticate(token) == device


def test_wallet_adjustment_view(client, superadmin):
    wallet = services.wallet_for(UserFactory())
    url = reverse("admin:wallets_wallet_adjust", args=[wallet.pk])
    assert client.get(url).status_code == 200
    response = client.post(url, {"amount_kz": 750, "memo": "Promo"})
    assert response.status_code == 302
    wallet.refresh_from_db()
    assert wallet.balance_kz == 750
    entry = LedgerEntry.objects.get(wallet=wallet)
    assert (entry.kind, entry.actor) == (LedgerEntry.Kind.ADJUSTMENT, f"admin:{superadmin.pk}")
    # débito acima do disponível é recusado
    response = client.post(url, {"amount_kz": -5000, "memo": "x"})
    assert response.status_code == 200
    wallet.refresh_from_db()
    assert wallet.balance_kz == 750


def test_pi_form_offers_only_printers_without_pi(client, superadmin):
    taken = EdgeDeviceFactory().printer
    free = PrinterFactory()
    form = client.get(reverse("admin:devices_edgedevice_add")).context["adminform"].form
    choices = set(form.fields["printer"].queryset)
    assert free in choices
    assert taken not in choices
    # ao editar o próprio Pi, a sua impressora continua disponível
    url = reverse("admin:devices_edgedevice_change", args=[taken.edge_device.pk])
    form = client.get(url).context["adminform"].form
    assert taken in set(form.fields["printer"].queryset)
