"""Imagens das páginas: geradas na análise, servidas por URL assinado, só ao dono."""

import shutil
from datetime import timedelta

import pytest
from django.core import signing
from django.utils import timezone

from apps.jobs import library, previews, tasks
from apps.jobs.models import Document, Job
from common import storage
from tests.docs import make_pdf, page
from tests.factories import JobFactory, UserFactory

pytestmark = pytest.mark.django_db
needs_tools = pytest.mark.skipif(not shutil.which("gs"), reason="needs ghostscript")


@pytest.fixture
def document(s3, customer, tmp_path, django_capture_on_commit_callbacks):
    pdf = make_pdf(tmp_path / "doc.pdf", [page(), page(box="red"), page()])
    with django_capture_on_commit_callbacks(execute=True), open(pdf, "rb") as fh:
        doc = library.save_document(customer, fh, "relatorio.pdf", pdf.stat().st_size)
    return Document.objects.get(pk=doc.pk)


@needs_tools
def test_document_previews_in_portal_and_kiosk(document, customer, client, settings):
    assert document.analysis["preview_pages"] == 3
    urls = previews.preview_urls(document)
    assert len(urls) == 3 and urls == previews.preview_urls(document)  # URLs estáveis (cache)

    image = client.get(urls[0])
    assert image.status_code == 200 and image["Content-Type"] == "image/jpeg"
    assert image.content[:2] == b"\xff\xd8"

    client.force_login(customer)
    page_view = client.get(f"/ficheiros/{document.pk}/")
    assert page_view.status_code == 200 and urls[2].encode() in page_view.content
    listing = client.get("/ficheiros/")
    assert urls[0].encode() in listing.content

    other = UserFactory()
    client.force_login(other)
    assert client.get(f"/ficheiros/{document.pk}/").status_code == 404

    library.delete_document(document)
    assert not storage.object_size(f"previews/documents/{document.pk}/p-001.jpg")


@needs_tools
def test_job_previews_survive_file_purge(document, kiosk, customer, client, settings):
    job = library.job_from_document(document, kiosk, actor="test")
    tasks.analyze_job(str(job.pk))
    job.refresh_from_db()
    assert job.analysis["preview_pages"] == 3
    from apps.jobs.api import JobSerializer

    assert len(JobSerializer(job).data["preview_urls"]) == 3

    Job.objects.filter(pk=job.pk).update(
        state="COMPLETED", finished_at=timezone.now() - timedelta(days=2)
    )
    tasks.purge_job_files()
    assert storage.object_size(f"previews/jobs/{job.pk}/p-001.jpg")

    client.force_login(customer)
    history = client.get("/impressoes/")
    assert f"/impressoes/{job.pk}/".encode() in history.content
    detail = client.get(f"/impressoes/{job.pk}/")
    assert detail.status_code == 200 and detail.content.count(b"/api/v1/previews/") == 3

    Job.objects.filter(pk=job.pk).update(created_at=timezone.now() - timedelta(days=31))
    assert tasks.purge_previews() == 1
    job.refresh_from_db()
    assert previews.preview_urls(job) == []
    assert not storage.object_size(f"previews/jobs/{job.pk}/p-001.jpg")
    gone = client.get(f"/impressoes/{job.pk}/").content
    assert b"/api/v1/previews/" not in gone and b"30" in gone


def test_preview_tokens_are_checked(client, s3):
    assert client.get("/api/v1/previews/garbage").status_code == 404
    forged = signing.Signer(salt=previews.SALT).sign_object(
        {"k": "jobs/x/document.pdf", "e": 2**40}
    )
    assert client.get(f"/api/v1/previews/{forged}").status_code == 404  # só previews/
    expired = signing.Signer(salt=previews.SALT).sign_object({"k": "previews/jobs/x/p-001.jpg", "e": 1})
    assert client.get(f"/api/v1/previews/{expired}").status_code == 404


def test_job_without_previews(customer, kiosk, client):
    job = JobFactory(customer=customer, kiosk=kiosk)
    assert previews.preview_urls(job) == []
    client.force_login(customer)
    assert client.get(f"/impressoes/{job.pk}/").status_code == 200
