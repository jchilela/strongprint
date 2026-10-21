"""Cópia completa com digitalizador e impressora simulados."""

import shutil

import pytest
from rest_framework.test import APIClient

from apps.devices.models import KioskPairing, Printer
from apps.jobs.models import Job
from apps.wallets import services as wallet
from domain.jobs.state_machine import JobState
from domain.printing.fake import FakePrinterGateway
from domain.scanning.gateway import FakeScannerGateway
from tests.factories import EdgeDeviceFactory, UserFactory

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(not shutil.which("gs"), reason="needs ghostscript"),
]


@pytest.fixture(autouse=True)
def fakes():
    FakeScannerGateway.reset()
    FakePrinterGateway.reset()
    yield
    FakeScannerGateway.reset()
    FakePrinterGateway.reset()


@pytest.fixture
def device(kiosk):
    Printer.objects.filter(pk=kiosk.printer.pk).update(status="READY")
    return EdgeDeviceFactory(printer=kiosk.printer)


@pytest.fixture
def screen(device):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Device {device.plain_token}")
    return client


@pytest.fixture
def session(screen, kiosk):
    user = UserFactory(full_name="Ana")
    wallet.credit(wallet.wallet_for(user), 5000, idempotency_key="seed", actor="t")
    pairing = screen.post("/api/v1/kiosk/pairings").json()
    KioskPairing.objects.filter(pk=pairing["id"]).update(status="CLAIMED", user=user)
    token = screen.get(f"/api/v1/kiosk/pairings/{pairing['id']}").json()["token"]
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Kiosk {token}")
    client.user = user
    return client


def test_scanner_state(screen, kiosk):
    data = screen.get("/api/v1/kiosk/scanner").json()
    assert data["available"] and data["adf_loaded"]
    FakeScannerGateway.configure(str(kiosk.printer.pk), offline=True)
    assert screen.get("/api/v1/kiosk/scanner").json() == {
        "available": False,
        "error": "scanner_offline",
    }


def test_full_copy_flow(session, kiosk, s3, django_capture_on_commit_callbacks):
    # 1.º lote pelo alimentador (2 folhas, frente e verso = 4 páginas)
    with django_capture_on_commit_callbacks(execute=True):
        started = session.post(
            f"/api/v1/kiosks/{kiosk.code}/copies", {"duplex_original": True}, format="json"
        )
    assert started.status_code == 201, started.data
    job_id = started.data["id"]
    job = session.get(f"/api/v1/jobs/{job_id}").json()
    assert job["state"] == "CREATED" and job["scan"]["status"] == "ready"
    assert job["scan"]["pages"] == 4 and job["options"]["duplex"] is True

    # 2.º lote pelo vidro (1 página)
    with django_capture_on_commit_callbacks(execute=True):
        assert session.post(f"/api/v1/jobs/{job_id}/scan", {}, format="json").status_code == 202
    job = session.get(f"/api/v1/jobs/{job_id}").json()
    assert job["scan"]["pages"] == 5 and job["scan"]["batches"] == 2
    assert job["scan"]["last_source"] == "platen"

    # terminar → análise → preço
    with django_capture_on_commit_callbacks(execute=True):
        finished = session.post(f"/api/v1/jobs/{job_id}/scan/finish")
    assert finished.status_code == 202
    job = session.get(f"/api/v1/jobs/{job_id}").json()
    assert job["state"] == "ANALYZED" and job["pages"] == 5
    assert job["original_filename"] == "Cópia (5 pág.)"
    assert job["total_kz"] == 150  # 5 páginas P&B a 30 Kz (preço de cópia)

    # imprimir: igual a uma impressão normal
    with django_capture_on_commit_callbacks(execute=True):
        printed = session.post(f"/api/v1/jobs/{job_id}/checkout")
    assert printed.status_code == 200
    final = Job.objects.get(pk=job_id)
    assert final.state == JobState.COMPLETED and final.pages_printed == 5
    assert final.source == Job.Source.COPY
    assert wallet.wallet_for(session.user).balance_kz == 4850


def test_copy_errors(session, kiosk, s3, django_capture_on_commit_callbacks):
    FakeScannerGateway.configure(str(kiosk.printer.pk), adf_loaded=False)
    with django_capture_on_commit_callbacks(execute=True):
        started = session.post(
            f"/api/v1/kiosks/{kiosk.code}/copies", {"source": "adf"}, format="json"
        )
    job_id = started.data["id"]
    job = session.get(f"/api/v1/jobs/{job_id}").json()
    assert job["scan"] == {**job["scan"], "status": "error", "error": "adf_empty"}

    finish = session.post(f"/api/v1/jobs/{job_id}/scan/finish")
    assert (finish.status_code, finish.data["error"]) == (400, "nothing_scanned")

    Job.objects.filter(pk=job_id).update(scan={"status": "scanning"})
    again = session.post(f"/api/v1/jobs/{job_id}/scan", {}, format="json")
    assert (again.status_code, again.data["error"]) == (400, "scan_in_progress")

    cancelled = session.post(f"/api/v1/jobs/{job_id}/cancel")
    assert cancelled.data["state"] == "CANCELLED"
    after = session.post(f"/api/v1/jobs/{job_id}/scan", {}, format="json")
    assert after.data["error"] == "not_a_copy_in_progress"


def test_scan_endpoints_reject_normal_jobs(session, kiosk):
    created = session.post(
        f"/api/v1/kiosks/{kiosk.code}/jobs", {"filename": "a.pdf"}, format="json"
    )
    response = session.post(f"/api/v1/jobs/{created.data['id']}/scan", {}, format="json")
    assert response.data["error"] == "not_a_copy_in_progress"
