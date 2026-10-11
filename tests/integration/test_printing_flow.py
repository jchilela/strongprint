"""Fluxo completo com o FakePrinterGateway: checkout → impressão → verificação → liquidação."""

import io
import shutil

import pytest

from apps.jobs import printing, tasks
from apps.jobs.models import Job
from apps.wallets import services as wallet
from apps.wallets.models import LedgerEntry, WalletHold
from domain.jobs.state_machine import JobState
from domain.printing.fake import FakePrinterGateway
from domain.printing.gateway import PrinterState
from tests.docs import make_pdf, page

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(not shutil.which("gs"), reason="needs ghostscript"),
]


@pytest.fixture(autouse=True)
def fake_printers():
    FakePrinterGateway.reset()
    yield
    FakePrinterGateway.reset()


@pytest.fixture
def ready_job(api, kiosk, customer, s3, tmp_path, django_capture_on_commit_callbacks):
    """Trabalho ANALYZED: 3 páginas (preto 15%, cor pouca, cor total), 2 cópias, a cores.

    A cores, todas as páginas pagam o escalão de cor da cobertura: 350 + 200 + 500.
    """
    created = api.post(f"/api/v1/kiosks/{kiosk.code}/jobs", {"filename": "a.pdf"}, format="json")
    path = make_pdf(
        tmp_path / "a.pdf",
        [page(box="black"), page(box="red", box_area=(50, 50, 150, 150)), page(background="blue")],
    )
    upload = io.BytesIO(path.read_bytes())
    upload.name = "a.pdf"
    with django_capture_on_commit_callbacks(execute=True):
        api.post(f"/api/v1/jobs/{created.data['id']}/file", {"file": upload}, format="multipart")
    api.patch(
        f"/api/v1/jobs/{created.data['id']}/options", {"color": True, "copies": 2}, format="json"
    )
    wallet.credit(wallet.wallet_for(customer), 5000, idempotency_key="seed", actor="test")
    job = Job.objects.get(pk=created.data["id"])
    assert job.state == JobState.ANALYZED and job.total_kz == (350 + 200 + 500) * 2
    return job


def checkout(api, job, capture):
    with capture(execute=True):
        response = api.post(f"/api/v1/jobs/{job.id}/checkout")
    job.refresh_from_db()
    return response, job


def test_completed_print_captures_everything(
    api, ready_job, customer, kiosk, django_capture_on_commit_callbacks
):
    response, job = checkout(api, ready_job, django_capture_on_commit_callbacks)
    assert response.status_code == 200, response.data
    assert job.state == JobState.COMPLETED
    assert (job.counter_before, job.counter_after, job.pages_printed) == (10_000, 10_006, 6)
    assert job.printer == kiosk.printer and job.printer_job_id

    w = wallet.wallet_for(customer)
    assert (w.balance_kz, w.held_kz) == (5000 - 2100, 0)
    capture = LedgerEntry.objects.get(kind=LedgerEntry.Kind.CAPTURE)
    assert (capture.amount_kz, capture.kiosk) == (-2100, kiosk)

    sent = FakePrinterGateway(str(kiosk.printer.pk)).state["submitted"][0]
    assert (sent["pages"], sent["copies"], sent["color"]) == (3, 2, True)
    trail = list(job.events.values_list("to_state", flat=True))
    assert trail[-4:] == ["RESERVED", "QUEUED", "PRINTING", "COMPLETED"]


def test_partial_print_charges_printed_faces(
    api, ready_job, customer, kiosk, django_capture_on_commit_callbacks
):
    FakePrinterGateway.configure(str(kiosk.printer.pk), behaviour="partial", partial_faces=4)
    _, job = checkout(api, ready_job, django_capture_on_commit_callbacks)
    assert (job.state, job.pages_printed) == (JobState.PARTIAL, 4)
    hold = WalletHold.objects.get(job=job)
    assert hold.captured_kz == 350 + 200 + 500 + 350  # 1.ª cópia + 1.ª página da 2.ª
    w = wallet.wallet_for(customer)
    assert (w.balance_kz, w.held_kz) == (5000 - 1400, 0)


def test_failed_print_releases_everything(
    api, ready_job, customer, kiosk, django_capture_on_commit_callbacks
):
    FakePrinterGateway.configure(str(kiosk.printer.pk), behaviour="fail")
    _, job = checkout(api, ready_job, django_capture_on_commit_callbacks)
    assert (job.state, job.failure_reason) == (JobState.FAILED, "printer_aborted")
    w = wallet.wallet_for(customer)
    assert (w.balance_kz, w.held_kz) == (5000, 0)


def test_checkout_refused_when_printer_not_ready(api, ready_job, customer, kiosk):
    FakePrinterGateway.configure(str(kiosk.printer.pk), paper_out=True, state=PrinterState.ERROR)
    response = api.post(f"/api/v1/jobs/{ready_job.id}/checkout")
    assert (response.status_code, response.data["reason"]) == (409, "paper_out")
    FakePrinterGateway.configure(str(kiosk.printer.pk), behaviour="offline")
    assert api.post(f"/api/v1/jobs/{ready_job.id}/checkout").data["reason"] == "printer_offline"
    ready_job.refresh_from_db()
    assert ready_job.state == JobState.ANALYZED
    assert wallet.wallet_for(customer).held_kz == 0


def test_busy_printer_queues_and_prints_when_free(
    api, ready_job, customer, kiosk, django_capture_on_commit_callbacks
):
    """Impressora a imprimir outro trabalho (não aceita mais por agora): não é erro."""
    key = str(kiosk.printer.pk)
    FakePrinterGateway.configure(
        key, state=PrinterState.BUSY, accepting=False, reasons=["spool-area-full-report"]
    )
    with django_capture_on_commit_callbacks(execute=False):
        response = api.post(f"/api/v1/jobs/{ready_job.id}/checkout")
    assert response.status_code == 200, response.data
    ready_job.refresh_from_db()
    assert ready_job.state == JobState.QUEUED
    with pytest.raises(printing.PrinterNotReady) as exc:
        printing.start(ready_job)
    assert exc.value.reason == "busy"

    FakePrinterGateway.configure(key, state=PrinterState.READY, accepting=True, reasons=[])
    assert tasks.print_job(str(ready_job.pk)) == JobState.COMPLETED


def test_no_printer_in_kiosk(ready_job, kiosk):
    from apps.partners.models import Kiosk

    printer = kiosk.printer
    printer.kiosk = None
    printer.name = "RESERVA-01"
    printer.save()
    fresh = Kiosk.objects.get(pk=kiosk.pk)
    with pytest.raises(printing.PrinterNotReady) as exc:
        printing.ensure_ready(getattr(fresh, "printer", None))
    assert exc.value.reason == "no_printer"


def queued(api, job, capture):
    """Põe o trabalho em QUEUED sem correr a tarefa de impressão."""
    with capture(execute=False):
        api.post(f"/api/v1/jobs/{job.id}/checkout")
    job.refresh_from_db()
    assert job.state == JobState.QUEUED
    return job


def test_resume_after_crash_never_resubmits(
    api, ready_job, kiosk, django_capture_on_commit_callbacks
):
    job = queued(api, ready_job, django_capture_on_commit_callbacks)
    gateway = FakePrinterGateway(str(kiosk.printer.pk))
    # Simula: worker enviou o trabalho e caiu antes de verificar.
    job = printing.services.transition(
        job.pk, JobState.PRINTING, actor="t", printer=kiosk.printer, counter_before=10_000
    )
    from domain.printing.gateway import PrintRequest

    pid = gateway.submit(
        PrintRequest(
            pdf=printing.build_print_pdf(job), job_name="x", color=True, duplex=False, copies=2
        )
    )
    Job.objects.filter(pk=job.pk).update(printer_job_id=pid)

    assert tasks.print_job(str(job.pk)) == JobState.COMPLETED
    assert len(gateway.state["submitted"]) == 1  # nada foi reenviado


def test_resume_without_printer_job_id_uses_counter(
    api, ready_job, kiosk, django_capture_on_commit_callbacks
):
    job = queued(api, ready_job, django_capture_on_commit_callbacks)
    printing.services.transition(
        job.pk, JobState.PRINTING, actor="t", printer=kiosk.printer, counter_before=10_000
    )
    FakePrinterGateway.configure(str(kiosk.printer.pk), counter=10_002)  # saíram 2 faces
    assert tasks.print_job(str(job.pk)) == JobState.PARTIAL
    assert FakePrinterGateway(str(kiosk.printer.pk)).state["submitted"] == []


def test_stuck_printer_times_out(
    api, ready_job, kiosk, settings, django_capture_on_commit_callbacks
):
    settings.PRINT_JOB_TIMEOUT_SECONDS = 0
    FakePrinterGateway.configure(str(kiosk.printer.pk), behaviour="stuck")
    _, job = checkout(api, ready_job, django_capture_on_commit_callbacks)
    assert job.state == JobState.FAILED


def test_print_task_ignores_finished_jobs(ready_job):
    assert tasks.print_job(str(ready_job.pk)) == JobState.ANALYZED


def test_resume_stuck_printing_requeues(
    api, ready_job, kiosk, settings, django_capture_on_commit_callbacks
):
    settings.PRINT_JOB_TIMEOUT_SECONDS = 0
    job = queued(api, ready_job, django_capture_on_commit_callbacks)
    printing.services.transition(
        job.pk, JobState.PRINTING, actor="t", printer=kiosk.printer, counter_before=10_000
    )
    Job.objects.filter(pk=job.pk).update(updated_at=job.updated_at.replace(year=2020))
    assert tasks.resume_stuck_printing() == 1
    job.refresh_from_db()
    assert job.state == JobState.FAILED  # contador não mexeu → nada impresso, valor libertado
