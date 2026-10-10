import json
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from apps.devices.models import EdgeDevice
from tests.factories import EdgeDeviceFactory

pytestmark = pytest.mark.django_db
PUB = "xTIBA5rboUvnH4htodjb6e697QjLERt1NAB4mZqp8Dg="


def provision(*args):
    out = StringIO()
    call_command("provision_pi", *args, stdout=out)
    return json.loads(out.getvalue())


def test_provision_assigns_slot_and_updates_admin(settings):
    settings.VPN_SERVER_PUBLIC_KEY = "SERVERKEY="
    settings.HOST_API = "api.sp.test"
    settings.HOST_SCREEN = "ecra.sp.test"
    device = EdgeDeviceFactory()
    data = provision(
        device.name,
        "--public-key",
        PUB,
        "--lan-ip",
        "192.168.1.238",
        "--ssh-user",
        "julio",
        "--printer-lan-ip",
        "192.168.1.50",
        "--issue-token",
    )
    device.refresh_from_db()
    assert data["slot"] == 1 and data["pi_vpn_ip"] == "10.77.1.1"
    assert data["printer_vpn_ip"] == "10.77.1.2" and data["subnet"] == "10.77.1.0/24"
    assert data["api_url"] == "https://api.sp.test"
    assert data["screen_url"] == "https://ecra.sp.test/ecra/"
    assert EdgeDevice.authenticate(data["token"]) == device
    assert (device.lan_ip, device.ssh_user, device.wg_public_key) == ("192.168.1.238", "julio", PUB)
    printer = device.printer
    printer.refresh_from_db()
    assert (printer.vpn_ip, printer.lan_ip) == ("10.77.1.2", "192.168.1.50")
    assert printer.effective_ipp_uri == "ipp://10.77.1.2/ipp/print"

    # reinstalação: mantém o slot e o token
    again = provision(device.name, "--public-key", PUB)
    assert again["slot"] == 1 and "token" not in again
    assert EdgeDevice.authenticate(data["token"]) == device

    other = EdgeDeviceFactory()
    assert provision(other.name, "--public-key", PUB)["slot"] == 2


def test_provision_validation():
    device = EdgeDeviceFactory()
    with pytest.raises(CommandError):
        provision(device.name, "--public-key", "not-a-key")
    with pytest.raises(CommandError):
        provision("NOPE-PI", "--public-key", PUB)
