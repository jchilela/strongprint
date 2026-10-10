import pytest
from django.core.exceptions import ValidationError

from apps.devices.models import Printer
from apps.partners.models import Kiosk, Partner, Site
from tests.factories import EdgeDeviceFactory, PrinterFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def imetro():
    partner = Partner.objects.create(name="Instituto Metropolitano de Angola", code="imetro")
    site = Site.objects.create(partner=partner)
    kiosk = Kiosk.objects.create(site=site)
    return partner, site, kiosk


def test_hierarchy_generates_codes_and_names(imetro):
    partner, site, kiosk = imetro
    assert partner.code == "IMETRO"
    assert (site.number, site.name, site.code) == (1, "Site-01", "IMETRO-S01")
    assert (kiosk.number, kiosk.name, kiosk.code) == (1, "Kiosk-01", "IMETRO-S01-K01")


def test_numbers_are_sequential_per_parent(imetro):
    partner, site, _ = imetro
    assert Kiosk.objects.create(site=site).code == "IMETRO-S01-K02"
    site2 = Site.objects.create(partner=partner)
    assert site2.code == "IMETRO-S02"
    assert Kiosk.objects.create(site=site2).code == "IMETRO-S02-K01"


def test_equipment_named_after_kiosk(imetro):
    *_, kiosk = imetro
    printer = PrinterFactory(kiosk=kiosk)
    pi = EdgeDeviceFactory(printer=printer)
    assert printer.name == "IMETRO-S01-K01-PRN"
    assert pi.name == "IMETRO-S01-K01-PI"
    assert pi.kiosk == kiosk


def test_renumbering_propagates_to_equipment(imetro):
    partner, site, kiosk = imetro
    printer = PrinterFactory(kiosk=kiosk)
    pi = EdgeDeviceFactory(printer=printer)
    partner.code = "IMT"
    partner.save()
    kiosk.refresh_from_db()
    printer.refresh_from_db()
    pi.refresh_from_db()
    assert kiosk.code == "IMT-S01-K01"
    assert printer.name == "IMT-S01-K01-PRN"
    assert pi.name == "IMT-S01-K01-PI"


def test_spare_printer_keeps_its_name():
    spare = Printer.objects.create(kiosk=None, name="RESERVA-01", vpn_ip="10.0.0.9")
    pi = EdgeDeviceFactory(printer=spare)
    assert spare.name == "RESERVA-01"
    assert pi.name == "RESERVA-01-PI"
    with pytest.raises(ValidationError):
        Printer(kiosk=None, name="", vpn_ip="10.0.0.8").clean()


def test_partner_code_validation():
    with pytest.raises(ValidationError):
        Partner(name="X", code="IM-01").full_clean()
