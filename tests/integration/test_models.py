import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.accounts.models import User
from apps.accounts.phone import normalize_phone
from apps.devices.models import EdgeDevice
from apps.partners.models import PriceTable
from tests.factories import (
    EdgeDeviceFactory,
    KioskFactory,
    PartnerFactory,
    PrinterFactory,
    SiteFactory,
)

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("raw", ["923456789", "+244 923 456 789", "00244923456789"])
def test_phone_normalization(raw):
    assert normalize_phone(raw) == "+244923456789"


@pytest.mark.parametrize("raw", ["123", "abc", ""])
def test_phone_invalid(raw):
    with pytest.raises(ValidationError):
        normalize_phone(raw)


def test_user_manager_normalizes_and_authenticates_by_phone():
    user = User.objects.create_user("923 456 789", password="s3cret-pass")
    assert user.phone == "+244923456789"
    assert User.objects.get_by_natural_key("923456789") == user
    admin = User.objects.create_superuser("912345678", password="x-pass-123")
    assert admin.is_staff and admin.is_superuser
    assert not User.objects.create_user("934567890").has_usable_password()


def test_edge_device_token_only_hash_stored():
    device = EdgeDeviceFactory()
    token = device.plain_token
    device.refresh_from_db()
    assert token not in device.device_token_hash
    assert device.token_prefix == token[:8]
    assert EdgeDevice.authenticate(token) == device
    assert device.kiosk == device.printer.kiosk
    assert EdgeDevice.authenticate("wrong") is None
    assert EdgeDevice.authenticate("") is None
    device.is_active = False
    device.save()
    assert EdgeDevice.authenticate(token) is None


def test_printer_defaults():
    printer = PrinterFactory(vpn_ip="10.147.17.20")
    assert printer.effective_ipp_uri == "ipp://10.147.17.20/ipp/print"
    assert printer.panel_enabled is False
    assert printer.capabilities == {"color": True, "duplex": True, "a3": False, "adf": True}
    printer.ipp_uri = "ipps://10.147.17.20/ipp/print"
    assert printer.effective_ipp_uri == "ipps://10.147.17.20/ipp/print"


def test_kiosk_partner_shortcut():
    kiosk = KioskFactory()
    assert kiosk.partner == kiosk.site.partner


def test_global_price_table_seeded():
    table = PriceTable.objects.get(partner=None, site=None, kiosk=None)
    assert (table.bw_page_kz, table.color_low_kz, table.color_mid_kz, table.color_high_kz) == (
        100,
        200,
        350,
        500,
    )
    assert (table.color_low_max_pct, table.color_mid_max_pct) == (10, 30)


def test_only_one_global_price_table():
    with pytest.raises(IntegrityError):
        PriceTable.objects.create(bw_page_kz=1)


def test_price_table_single_scope_constraint():
    partner = PartnerFactory()
    site = SiteFactory(partner=partner)
    with pytest.raises(IntegrityError):
        PriceTable.objects.create(partner=partner, site=site, bw_page_kz=90)


def test_price_table_clean_rules():
    partner = PartnerFactory()
    PriceTable(partner=partner, bw_page_kz=90).clean()  # parcial é permitido num âmbito
    with pytest.raises(ValidationError):
        PriceTable(bw_page_kz=90).clean()  # global incompleta
    with pytest.raises(ValidationError):
        PriceTable(partner=partner, site=SiteFactory()).clean()
    with pytest.raises(ValidationError):
        PriceTable(partner=partner, color_low_max_pct=40, color_mid_max_pct=30).clean()
