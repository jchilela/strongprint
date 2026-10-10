import tempfile
from datetime import timedelta
from pathlib import Path

import structlog
from botocore.exceptions import BotoCoreError, ClientError
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from common import storage
from domain.analysis.analyzer import DocumentAnalyzer
from domain.analysis.types import AnalysisError
from domain.jobs.state_machine import InvalidTransition, JobState
from domain.pricing.service import InvalidOptions, PrintOptions

from . import services
from .models import Job

log = structlog.get_logger(__name__)

EXPIRABLE = [JobState.CREATED, JobState.FILE_RECEIVED, JobState.ANALYZED]
TERMINAL = [
    JobState.COMPLETED,
    JobState.PARTIAL,
    JobState.FAILED,
    JobState.REJECTED,
    JobState.CANCELLED,
    JobState.EXPIRED,
]


def original_key(job: Job) -> str:
    return job.file_key


def document_key(job: Job) -> str:
    return f"jobs/{job.id}/document.pdf"


def analyzer() -> DocumentAnalyzer:
    return DocumentAnalyzer(
        color_threshold_pct=settings.ANALYSIS_COLOR_THRESHOLD_PCT,
        max_pages=settings.MAX_DOCUMENT_PAGES,
    )


@shared_task(
    queue="analysis",
    autoretry_for=(BotoCoreError, ClientError),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def analyze_job(job_id: str) -> str:
    """FILE_RECEIVED → ANALYZED (com preço) ou REJECTED. Idempotente."""
    structlog.contextvars.bind_contextvars(job_id=str(job_id))
    job = Job.objects.select_related("kiosk__site").get(pk=job_id)
    if job.state != JobState.FILE_RECEIVED:
        log.info("analysis.skipped", state=job.state)
        return job.state

    with tempfile.TemporaryDirectory(prefix="sp-analysis-") as tmp:
        workdir = Path(tmp)
        source = workdir / "source"
        storage.download_file(original_key(job), source)
        try:
            pdf, analysis = analyzer().analyze(source, job.original_filename, workdir)
        except AnalysisError as exc:
            log.warning("analysis.rejected", code=exc.code, reason=str(exc))
            try:
                services.transition(
                    job.pk,
                    JobState.REJECTED,
                    actor="task:analyze",
                    data={"code": exc.code},
                    failure_reason=exc.code,
                )
            except InvalidTransition:
                pass
            return JobState.REJECTED
        storage.upload_file(pdf, document_key(job), "application/pdf")

    job.analysis = analysis.to_dict()
    options = PrintOptions.from_dict(job.options)
    try:
        breakdown = services.quote(job, options)
    except InvalidOptions:
        # Intervalo de páginas pedido antes da análise já não cabe no documento.
        options = PrintOptions.from_dict({**options.to_dict(), "page_range": None})
        breakdown = services.quote(job, options)
    try:
        services.transition(
            job.pk,
            JobState.ANALYZED,
            actor="task:analyze",
            data={"pages": analysis.page_count, "color_pages": analysis.color_pages},
            analysis=job.analysis,
            pages=analysis.page_count,
            options=options.to_dict(),
            price_breakdown=breakdown,
            total_kz=breakdown["total_kz"],
            expires_at=services.session_deadline(),
        )
    except InvalidTransition:
        log.info("analysis.discarded", reason="job changed state meanwhile")
        return Job.objects.get(pk=job_id).state
    log.info("analysis.done", pages=analysis.page_count, total_kz=breakdown["total_kz"])
    return JobState.ANALYZED


@shared_task(queue="default")
def expire_jobs() -> int:
    """Trabalhos não confirmados dentro do prazo → EXPIRED."""
    count = 0
    stale = Job.objects.filter(state__in=EXPIRABLE, expires_at__lt=timezone.now())
    for job_id in stale.values_list("pk", flat=True):
        try:
            services.finish(job_id, JobState.EXPIRED, actor="task:expire")
            count += 1
        except InvalidTransition:
            continue
    if count:
        log.info("jobs.expired", count=count)
    return count


@shared_task(queue="default")
def fail_stale_queued_jobs() -> int:
    """Trabalhos em fila há demasiado tempo → FAILED, libertando o valor cativo."""
    limit = timezone.now() - timedelta(minutes=settings.PRINT_QUEUE_TIMEOUT_MINUTES)
    count = 0
    for job_id in Job.objects.filter(state=JobState.QUEUED, updated_at__lt=limit).values_list(
        "pk", flat=True
    ):
        try:
            services.finish(
                job_id, JobState.FAILED, actor="task:queue-timeout", failure_reason="queue_timeout"
            )
            count += 1
        except InvalidTransition:
            continue
    if count:
        log.warning("jobs.queue_timeout", count=count)
    return count


@shared_task(
    queue="default", autoretry_for=(BotoCoreError, ClientError), retry_backoff=True, max_retries=3
)
def purge_job_files() -> int:
    """Apaga do armazenamento os ficheiros de trabalhos terminados há mais de N horas."""
    limit = timezone.now() - timedelta(hours=settings.FILE_RETENTION_HOURS)
    jobs = Job.objects.filter(
        state__in=TERMINAL, finished_at__lt=limit, file_purged_at__isnull=True
    )
    count = 0
    for job in jobs.only("pk")[:500]:
        storage.delete_prefix(f"jobs/{job.pk}/")
        Job.objects.filter(pk=job.pk).update(file_purged_at=timezone.now())
        count += 1
    if count:
        log.info("jobs.files_purged", count=count)
    return count
