from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.devices.models import EdgeDevice, Printer
from apps.monitoring import services, tasks
from apps.monitoring.models import Alert, Downtime
from apps.partners.models import Kiosk
from domain.printing.fake import FakePrinterGateway
from domain.printing.gateway import PrinterState
from tests.factories import EdgeDeviceFactory, JobFactory

pytestmark = pytest.mark.django_db
K = Alert.Kind


@pytest.fixture(autouse=True)
def fake_printers():
    FakePrinterGateway.reset()
    yield
    FakePrinterGateway.reset()


def open_kinds(kiosk):
    return set(kiosk.alerts.filter(resolved_at__isnull=True).values_list("kind", flat=True))


def test_poll_updates_printer_and_alerts(kiosk):
    printer = kiosk.printer
    key = str(printer.pk)
    assert tasks.poll_printers() == 1
    printer.refresh_from_db()
    assert (printer.status, printer.last_page_counter) == ("READY", 10_000)
    assert open_kinds(kiosk) == set()

    FakePrinterGateway.configure(
        key, paper_out=True, state=PrinterState.ERROR, toner={"black": 5, "cyan": 50}
    )
    services.poll_printer(printer)
    assert open_kinds(kiosk) == {K.PAPER_OUT, K.TONER_LOW}
    toner = kiosk.alerts.get(kind=K.TONER_LOW)
    assert toner.data == {"toner": {"black": 5}}

    FakePrinterGateway.configure(key, paper_out=False, reasons=["door-open-error"])
    services.poll_printer(printer)
    assert open_kinds(kiosk) == {K.TONER_LOW, K.PRINTER_ERROR}

    FakePrinterGateway.configure(key, behaviour="offline")
    services.poll_printer(printer)
    printer.refresh_from_db()
    assert printer.status == Printer.Status.OFFLINE
    assert K.PRINTER_OFFLINE in open_kinds(kiosk)

    FakePrinterGateway.configure(
        key,
        behaviour="complete",
        state=PrinterState.READY,
        reasons=[],
        toner={"black": 90},
    )
    services.poll_printer(printer)
    assert open_kinds(kiosk) == set()
    assert Alert.objects.count() == 4  # cada problema abriu um alerta e foi resolvido


def test_unexpected_pages_alert(kiosk):
    printer = kiosk.printer
    services.poll_printer(printer)
    FakePrinterGateway.configure(str(printer.pk), counter=10_005)
    services.poll_printer(printer)
    alert = kiosk.alerts.get(kind=K.UNEXPECTED_PAGES)
    assert alert.data == {"from": 10_000, "to": 10_005}

    # com o painel ligado, subir o contador é normal
    alert.delete()
    printer.refresh_from_db()
    printer.panel_enabled = True
    printer.save()
    FakePrinterGateway.configure(str(printer.pk), counter=10_010)
    services.poll_printer(printer)
    assert not kiosk.alerts.filter(kind=K.UNEXPECTED_PAGES).exists()


def test_counter_increase_during_job_is_expected(kiosk):
    printer = kiosk.printer
    services.poll_printer(printer)
    JobFactory(kiosk=kiosk, printer=printer, state="PRINTING")
    FakePrinterGateway.configure(str(printer.pk), counter=10_003)
    services.poll_printer(printer)
    assert not kiosk.alerts.filter(kind=K.UNEXPECTED_PAGES).exists()


def test_poll_survives_one_broken_printer(kiosk, monkeypatch):
    def boom(printer):
        raise RuntimeError("bug")

    monkeypatch.setattr(services, "poll_printer", boom)
    assert tasks.poll_printers() == 0


@pytest.fixture
def device(kiosk):
    return EdgeDeviceFactory(printer=kiosk.printer)


def pi(device):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Device {device.plain_token}")
    return client


def test_heartbeat_marks_online_and_returns_commands(device, kiosk):
    device.pending_command = EdgeDevice.Command.RELOAD
    device.save()
    response = pi(device).post(
        "/api/v1/edge/heartbeat", {"version": "1.2.0", "vpn_ip": "10.147.17.30"}, format="json"
    )
    assert response.status_code == 200, response.data
    assert response.data["commands"] == ["reload"]
    assert response.data["kiosk"]["code"] == kiosk.code
    device.refresh_from_db()
    kiosk.refresh_from_db()
    assert (device.version, device.vpn_ip, device.pending_command) == ("1.2.0", "10.147.17.30", "")
    assert device.last_heartbeat is not None
    assert kiosk.status == Kiosk.Status.ONLINE
    # o comando só é entregue uma vez
    assert pi(device).post("/api/v1/edge/heartbeat", {}, format="json").data["commands"] == []


def test_heartbeat_out_of_service(device, kiosk):
    Kiosk.objects.filter(pk=kiosk.pk).update(status=Kiosk.Status.OUT_OF_SERVICE)
    response = pi(device).post("/api/v1/edge/heartbeat", {}, format="json")
    assert response.data["commands"] == ["out_of_service"]
    kiosk.refresh_from_db()
    assert kiosk.status == Kiosk.Status.OUT_OF_SERVICE


def test_heartbeat_auth():
    client = APIClient()
    assert client.post("/api/v1/edge/heartbeat").status_code in (401, 403)
    client.credentials(HTTP_AUTHORIZATION="Device wrong-token")
    assert client.post("/api/v1/edge/heartbeat").status_code == 401
    client.credentials(HTTP_AUTHORIZATION="Device")
    assert client.post("/api/v1/edge/heartbeat").status_code == 401


def test_heartbeat_for_spare_printer(device):
    printer = device.printer
    printer.kiosk = None
    printer.name = "RESERVA-01"
    printer.save()
    response = pi(device).post("/api/v1/edge/heartbeat", {}, format="json")
    assert response.data["kiosk"] is None


def test_kiosk_goes_offline_and_back(device, kiosk):
    pi(device).post("/api/v1/edge/heartbeat", {}, format="json")
    EdgeDevice.objects.filter(pk=device.pk).update(
        last_heartbeat=timezone.now() - timedelta(minutes=5)
    )
    assert tasks.mark_offline_kiosks() == 1
    kiosk.refresh_from_db()
    assert kiosk.status == Kiosk.Status.OFFLINE
    assert K.KIOSK_OFFLINE in open_kinds(kiosk)
    assert tasks.mark_offline_kiosks() == 0  # já está offline

    pi(device).post("/api/v1/edge/heartbeat", {}, format="json")
    kiosk.refresh_from_db()
    assert kiosk.status == Kiosk.Status.ONLINE
    assert open_kinds(kiosk) == set()


def test_open_alert_is_deduplicated(kiosk):
    first = services.open_alert(K.TONER_LOW, kiosk, severity="warning", message="a")
    second = services.open_alert(K.TONER_LOW, kiosk, severity="warning", message="b")
    assert first.pk == second.pk
    second.refresh_from_db()
    assert second.message == "b" and second.is_open
    assert services.resolve_alert(K.TONER_LOW, kiosk) == 1
    assert services.resolve_alert(K.TONER_LOW, kiosk) == 0


def test_downtime_history(device, kiosk):
    device.last_heartbeat = timezone.now()
    device.save()
    Kiosk.objects.filter(pk=kiosk.pk).update(status=Kiosk.Status.ONLINE)
    printer = kiosk.printer
    key = str(printer.pk)
    services.poll_printer(printer)
    assert not Downtime.objects.exists()

    FakePrinterGateway.configure(key, paper_out=True, reasons=["media-empty-error"])
    services.poll_printer(printer)
    services.poll_printer(printer)  # o mesmo problema não abre outro período
    down = Downtime.objects.get()
    assert (down.reason, down.is_open, down.detail["reasons"]) == (
        "paper_out",
        True,
        ["media-empty-error"],
    )

    FakePrinterGateway.configure(key, paper_out=False, reasons=["media-jam-error"])
    services.poll_printer(printer)
    assert list(Downtime.objects.order_by("started_at").values_list("reason", flat=True)) == [
        "paper_out",
        "paper_jam",
    ]
    down.refresh_from_db()
    assert down.ended_at is not None

    FakePrinterGateway.configure(key, reasons=[], state=PrinterState.READY)
    services.poll_printer(printer)
    assert not Downtime.objects.filter(ended_at__isnull=True).exists()

    Kiosk.objects.filter(pk=kiosk.pk).update(status=Kiosk.Status.MAINTENANCE)
    assert tasks.mark_offline_kiosks() == 0
    assert Downtime.objects.get(ended_at__isnull=True).reason == "maintenance"
    assert Downtime.objects.first().duration_text.endswith(" s")


def test_kiosk_offline_downtime(device, kiosk):
    device.last_heartbeat = timezone.now() - timedelta(minutes=10)
    device.save()
    tasks.mark_offline_kiosks()
    assert Downtime.objects.get(ended_at__isnull=True).reason == "kiosk_offline"


def test_heartbeat_delivers_restart_vpn(device):
    device.pending_command = EdgeDevice.Command.RESTART_VPN
    device.save()
    response = pi(device).post("/api/v1/edge/heartbeat", {}, format="json")
    assert response.json()["commands"] == ["restart_vpn"]
