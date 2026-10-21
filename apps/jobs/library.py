"""Biblioteca "Meus ficheiros": documentos enviados antes (portal, WhatsApp) e
impressos depois em qualquer kiosk."""

import uuid
from datetime import timedelta

import structlog
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from common import storage
from domain.analysis.analyzer import SUPPORTED_EXTENSIONS, extension_of
from domain.analysis.types import AnalysisError
from domain.jobs.state_machine import JobState

from . import services
from .models import Document, Job

log = structlog.get_logger(__name__)

CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


def max_bytes() -> int:
    return settings.UPLOAD_MAX_MB * 1024 * 1024


def validate_upload(filename: str, size: int) -> str:
    ext = extension_of(filename)
    if ext not in SUPPORTED_EXTENSIONS:
        raise AnalysisError("unsupported_format", f"Unsupported file type: {ext or '?'}")
    if size > max_bytes():
        raise AnalysisError("file_too_large", f"Max {settings.UPLOAD_MAX_MB} MB")
    return ext


def save_document(owner, fileobj, filename: str, size: int, *, source=Job.Source.UPLOAD):
    from . import tasks

    ext = validate_upload(filename, size)
    doc_id = uuid.uuid4()
    key = f"documents/{doc_id}/original{ext}"
    content_type = CONTENT_TYPES.get(ext, "application/octet-stream")
    storage.upload_fileobj(fileobj, key, content_type)
    doc = Document.objects.create(
        id=doc_id,
        owner=owner,
        source=source,
        original_filename=filename[:255],
        file_key=key,
        file_size=size,
        mime_type=content_type,
        expires_at=timezone.now() + timedelta(days=settings.LIBRARY_RETENTION_DAYS),
    )
    transaction.on_commit(lambda: tasks.analyze_document.delay(str(doc.pk)))
    log.info("document.saved", document_id=str(doc.pk), size=size, source=source)
    return doc


def delete_document(doc: Document) -> None:
    from .previews import delete_previews

    storage.delete_prefix(f"documents/{doc.pk}/")
    delete_previews(doc)
    doc.delete()


def job_from_document(doc: Document, kiosk, *, actor: str) -> Job:
    """Cria um trabalho num kiosk a partir de um documento da biblioteca."""
    from . import tasks

    if doc.state != Document.State.READY:
        raise AnalysisError("document_not_ready", "Document is not ready")
    ext = extension_of(doc.file_key)
    job_id = uuid.uuid4()
    key = f"jobs/{job_id}/original{ext}"
    storage.s3_client().copy_object(
        Bucket=settings.S3_BUCKET,
        Key=key,
        CopySource={"Bucket": settings.S3_BUCKET, "Key": doc.file_key},
    )
    with transaction.atomic():
        job = services.create_job(
            id=job_id,
            kiosk=kiosk,
            customer=doc.owner,
            actor=actor,
            source=doc.source,
            original_filename=doc.original_filename,
            file_key=key,
            file_size=doc.file_size,
            mime_type=doc.mime_type,
            document=doc,
        )
        job = services.transition(
            job.pk, JobState.FILE_RECEIVED, actor=actor, data={"document": str(doc.pk)}
        )
        transaction.on_commit(lambda: tasks.analyze_job.delay(str(job.pk)))
    return job


def printed_info(documents) -> dict:
    """Por documento: quantas vezes saiu impresso (concluído ou parcial) e a última vez."""
    from django.db.models import Count, Max

    rows = (
        Job.objects.filter(
            document__in=documents,
            state__in=[JobState.COMPLETED, JobState.PARTIAL],
            pages_printed__gt=0,
        )
        .values("document")
        .annotate(count=Count("id"), last=Max("finished_at"))
    )
    return {row["document"]: {"count": row["count"], "last": row["last"]} for row in rows}
