"""Ficheiros já impressos em "Meus ficheiros": sinal ✓ e aviso antes de reimprimir."""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.jobs.library import printed_info
from apps.jobs.models import Document, Job, JobEvent
from domain.jobs.state_machine import JobState
from tests.factories import JobFactory, UserFactory

pytestmark = pytest.mark.django_db


def make_doc(owner, name="trabalho.pdf"):
    return Document.objects.create(
        owner=owner,
        original_filename=name,
        file_key=f"documents/x/{name}",
        state=Document.State.READY,
        pages=2,
        expires_at=timezone.now() + timedelta(days=7),
    )


def test_printed_info_counts_only_real_prints():
    user = UserFactory()
    doc, other = make_doc(user), make_doc(user, "outro.pdf")
    now = timezone.now()
    JobFactory(customer=user, document=doc, state=JobState.COMPLETED, pages_printed=2, finished_at=now - timedelta(hours=2))
    JobFactory(customer=user, document=doc, state=JobState.PARTIAL, pages_printed=1, finished_at=now)
    JobFactory(customer=user, document=doc, state=JobState.FAILED, pages_printed=0, finished_at=now)
    JobFactory(customer=user, document=other, state=JobState.CANCELLED)
    info = printed_info([doc, other])
    assert info[doc.pk]["count"] == 2 and info[doc.pk]["last"] == now
    assert other.pk not in info


def test_kiosk_files_show_printed_and_portal_badge(client):
    user = UserFactory(full_name="Ana")
    doc = make_doc(user)
    JobFactory(customer=user, document=doc, state=JobState.COMPLETED, pages_printed=2, finished_at=timezone.now())
    api = APIClient()
    api.force_authenticate(user)
    row = api.get("/api/v1/documents").json()[0]
    assert row["printed_count"] == 1 and row["last_printed_at"]

    client.force_login(user)
    assert "✓" in client.get("/ficheiros/").content.decode()


def test_migration_links_old_jobs_from_history():
    """Trabalhos antigos: a ligação ao documento vem do evento FILE_RECEIVED."""
    from importlib import import_module

    from django.apps import apps

    user = UserFactory()
    doc = make_doc(user)
    job = JobFactory(customer=user, state=JobState.COMPLETED, pages_printed=1)
    JobEvent.objects.create(job=job, from_state="CREATED", to_state="FILE_RECEIVED", actor="t", data={"document": str(doc.pk)})
    import_module("apps.jobs.migrations.0006_job_document").link_existing(apps, None)
    assert Job.objects.get(pk=job.pk).document_id == doc.pk
