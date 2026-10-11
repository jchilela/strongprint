"""Fluxo completo pela API: sessão → trabalho → ficheiro → preço → carteira → fila."""

import io
import shutil
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.jobs import tasks
from apps.jobs.models import Job
from apps.partners.models import PriceTable
from apps.partners.services import effective_price_rules
from apps.wallets import services as wallet
from apps.wallets.models import WalletHold
from common import storage
from domain.jobs.state_machine import JobState
from tests.docs import make_pdf, page
from tests.factories import JobFactory, KioskFactory, UserFactory

needs_tools = pytest.mark.skipif(not shutil.which("gs"), reason="needs ghostscript")
pytestmark = pytest.mark.django_db


def pdf_bytes(tmp_path, pages):
    path = make_pdf(tmp_path / "doc.pdf", pages)
    return io.BytesIO(path.read_bytes())


def new_job(api, kiosk, filename="trabalho.pdf"):
    response = api.post(f"/api/v1/kiosks/{kiosk.code}/jobs", {"filename": filename}, format="json")
    assert response.status_code == 201, response.data
    return response.data


def upload(api, job_id, content, name="trabalho.pdf", capture=None):
    content.name = name
    with capture(execute=True):
        return api.post(f"/api/v1/jobs/{job_id}/file", {"file": content}, format="multipart")


@pytest.fixture
def analyzed_job(api, kiosk, s3, tmp_path, django_capture_on_commit_callbacks):
    data = new_job(api, kiosk)
    doc = pdf_bytes(
        tmp_path, [page(box="black"), page(box="red", box_area=(50, 50, 150, 150)), page()]
    )
    response = upload(api, data["id"], doc, capture=django_capture_on_commit_callbacks)
    assert response.status_code == 202, response.data
    return Job.objects.get(pk=data["id"])


@needs_tools
def test_upload_analyzes_and_prices(analyzed_job, api):
    job = analyzed_job
    assert job.state == JobState.ANALYZED
    assert job.pages == 3
    assert job.price_breakdown["summary"]["bw"] == 3  # P&B por omissão
    assert job.total_kz == 300
    assert storage.object_size(f"jobs/{job.id}/document.pdf") > 0
    events = list(job.events.values_list("to_state", flat=True))
    assert events == ["CREATED", "FILE_RECEIVED", "ANALYZED"]

    detail = api.get(f"/api/v1/jobs/{job.id}").data
    assert detail["state"] == "ANALYZED" and detail["progress"] == 30
    assert detail["wallet"] == {"available_kz": 0, "missing_kz": 300}


@needs_tools
def test_options_reprice(analyzed_job, api):
    response = api.patch(
        f"/api/v1/jobs/{analyzed_job.id}/options",
        {"color": True, "copies": 2, "page_range": "1-2"},
        format="json",
    )
    assert response.status_code == 200, response.data
    assert response.data["price_breakdown"]["summary"] == {
        "bw": 0,
        "color_low": 1,
        "color_mid": 1,
        "color_high": 0,
    }
    assert response.data["total_kz"] == (350 + 200) * 2

    bad = api.patch(f"/api/v1/jobs/{analyzed_job.id}/options", {"page_range": "7"}, format="json")
    assert (bad.status_code, bad.data["error"]) == (400, "invalid_page_range")


@needs_tools
def test_checkout_requires_balance_then_queues(analyzed_job, api, customer):
    url = f"/api/v1/jobs/{analyzed_job.id}/checkout"
    response = api.post(url)
    assert response.status_code == 402
    assert response.data["missing_kz"] == 300

    w = wallet.wallet_for(customer)
    wallet.credit(w, 1000, idempotency_key="t", actor="test")
    response = api.post(url)
    assert response.status_code == 200, response.data
    assert response.data["state"] == "QUEUED"
    w.refresh_from_db()
    assert (w.balance_kz, w.held_kz) == (1000, 300)

    # opções já não podem mudar depois de cativar
    response = api.patch(f"/api/v1/jobs/{analyzed_job.id}/options", {"copies": 2}, format="json")
    assert (response.status_code, response.data["error"]) == (409, "invalid_state")

    # cancelar na fila liberta o valor
    response = api.post(f"/api/v1/jobs/{analyzed_job.id}/cancel")
    assert response.data["state"] == "CANCELLED"
    w.refresh_from_db()
    assert (w.balance_kz, w.held_kz) == (1000, 0)
    assert WalletHold.objects.get(job=analyzed_job).captured_kz == 0


@needs_tools
def test_rejected_file(api, kiosk, s3, django_capture_on_commit_callbacks):
    data = new_job(api, kiosk)
    response = upload(
        api, data["id"], io.BytesIO(b"%PDF-1.4 broken"), capture=django_capture_on_commit_callbacks
    )
    assert response.status_code == 202
    job = Job.objects.get(pk=data["id"])
    assert job.state == JobState.REJECTED
    assert job.failure_reason in {"corrupt_file", "empty_document"}


def test_create_job_validation(api, kiosk):
    response = api.post(f"/api/v1/kiosks/{kiosk.code}/jobs", {"filename": "x.exe"}, format="json")
    assert response.status_code == 400
    response = api.post(
        f"/api/v1/kiosks/{kiosk.code}/jobs",
        {"filename": "a.pdf", "size": 999 * 1024 * 1024},
        format="json",
    )
    assert response.status_code == 400
    assert api.post("/api/v1/kiosks/NOPE/jobs", {"filename": "a.pdf"}).status_code == 404


def test_upload_guards(api, kiosk, s3, django_capture_on_commit_callbacks):
    data = new_job(api, kiosk, "a.pdf")
    url = f"/api/v1/jobs/{data['id']}/file"
    assert api.post(url, {}, format="multipart").data["error"] == "file_missing"
    wrong = io.BytesIO(b"x")
    wrong.name = "a.docx"
    assert api.post(url, {"file": wrong}, format="multipart").data["error"] == "extension_mismatch"


def test_presigned_flow_uploaded_endpoint(api, kiosk, s3, django_capture_on_commit_callbacks):
    data = new_job(api, kiosk, "a.pdf")
    url = f"/api/v1/jobs/{data['id']}/uploaded"
    assert api.post(url).data["error"] == "file_missing"
    job = Job.objects.get(pk=data["id"])
    storage.s3_client().put_object(Bucket="strongprint-test", Key=job.file_key, Body=b"%PDF-1.4")
    with django_capture_on_commit_callbacks(execute=False):
        response = api.post(url)
    assert response.status_code == 202
    assert response.data["state"] == "FILE_RECEIVED"


def test_customers_only_see_their_jobs(api, kiosk):
    other = JobFactory(kiosk=kiosk, customer=UserFactory())
    assert api.get(f"/api/v1/jobs/{other.id}").status_code == 404
    assert APIClient().get(f"/api/v1/jobs/{other.id}").status_code in (401, 403)


def test_kiosk_session_token(api, kiosk, customer):
    response = api.post(f"/api/v1/kiosks/{kiosk.code}/sessions")
    assert response.status_code == 201
    token = response.data["token"]

    screen = APIClient()
    screen.credentials(HTTP_AUTHORIZATION=f"Kiosk {token}")
    assert screen.get("/api/v1/wallet").status_code == 200
    created = screen.post(f"/api/v1/kiosks/{kiosk.code}/jobs", {"filename": "a.pdf"}, format="json")
    assert created.status_code == 201
    assert Job.objects.get(pk=created.data["id"]).customer == customer

    other_kiosk = KioskFactory()
    wrong = screen.post(f"/api/v1/kiosks/{other_kiosk.code}/jobs", {"filename": "a.pdf"})
    assert wrong.status_code == 400

    other_job = JobFactory(kiosk=other_kiosk, customer=customer)
    assert screen.get(f"/api/v1/jobs/{other_job.id}").status_code == 404

    tampered = APIClient()
    tampered.credentials(HTTP_AUTHORIZATION=f"Kiosk {token}x")
    assert tampered.get("/api/v1/wallet").status_code in (401, 403)


def test_printer_capability_check(api, kiosk, customer):
    kiosk.printer.supports_color = False
    kiosk.printer.save()
    job = JobFactory(kiosk=kiosk, customer=customer, state=JobState.ANALYZED)
    response = api.patch(f"/api/v1/jobs/{job.id}/options", {"color": True}, format="json")
    assert response.status_code == 400


def test_expire_and_queue_timeout_tasks(customer, kiosk):
    past = timezone.now() - timedelta(minutes=1)
    stale = JobFactory(kiosk=kiosk, customer=customer, state=JobState.ANALYZED, expires_at=past)
    fresh = JobFactory(
        kiosk=kiosk,
        customer=customer,
        state=JobState.ANALYZED,
        expires_at=timezone.now() + timedelta(minutes=5),
    )
    assert tasks.expire_jobs() == 1
    stale.refresh_from_db()
    fresh.refresh_from_db()
    assert (stale.state, fresh.state) == (JobState.EXPIRED, JobState.ANALYZED)

    w = wallet.wallet_for(customer)
    wallet.credit(w, 500, idempotency_key="q", actor="t")
    queued = JobFactory(kiosk=kiosk, customer=customer, state=JobState.RESERVED, total_kz=200)
    wallet.place_hold(queued, actor="t")
    Job.objects.filter(pk=queued.pk).update(
        state=JobState.QUEUED, updated_at=timezone.now() - timedelta(hours=1)
    )
    assert tasks.fail_stale_queued_jobs() == 1
    queued.refresh_from_db()
    w.refresh_from_db()
    assert (queued.state, queued.failure_reason, w.held_kz) == (
        JobState.FAILED,
        "queue_timeout",
        0,
    )


def test_purge_job_files(s3, customer, kiosk):
    job = JobFactory(
        kiosk=kiosk,
        customer=customer,
        state=JobState.COMPLETED,
        finished_at=timezone.now() - timedelta(days=2),
    )
    storage.s3_client().put_object(Bucket="strongprint-test", Key=f"jobs/{job.id}/a.pdf", Body=b"x")
    assert tasks.purge_job_files() == 1
    job.refresh_from_db()
    assert job.file_purged_at is not None
    assert storage.object_size(f"jobs/{job.id}/a.pdf") is None
    assert tasks.purge_job_files() == 0


def test_effective_price_rules_inherit(kiosk):
    PriceTable.objects.create(partner=kiosk.site.partner, bw_page_kz=80)
    PriceTable.objects.create(kiosk=kiosk, color_high_kz=450, min_transaction_kz=0)
    rules = effective_price_rules(kiosk)
    assert (
        rules.bw_page_kz,
        rules.color_low_kz,
        rules.color_high_kz,
        rules.min_transaction_kz,
    ) == (
        80,
        200,
        450,
        0,
    )
