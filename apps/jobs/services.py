from datetime import timedelta

import structlog
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.partners.services import effective_price_rules
from apps.wallets import services as wallet
from apps.wallets.models import WalletHold
from domain.analysis.types import DocumentAnalysis
from domain.jobs.state_machine import (
    EDITABLE_STATES,
    FUNDS_HELD_STATES,
    InvalidTransition,
    JobState,
    is_terminal,
    validate_transition,
)
from domain.pricing.service import PrintOptions, price_job

from .models import Job, JobEvent

log = structlog.get_logger(__name__)


def session_deadline():
    return timezone.now() + timedelta(minutes=settings.JOB_SESSION_TIMEOUT_MINUTES)


def create_job(*, kiosk, customer, actor: str, **fields) -> Job:
    fields.setdefault("expires_at", session_deadline())
    with transaction.atomic():
        job = Job.objects.create(kiosk=kiosk, customer=customer, **fields)
        JobEvent.objects.create(job=job, from_state="", to_state=job.state, actor=actor)
    log.info("job.created", job_id=str(job.id), kiosk=kiosk.code, actor=actor)
    return job


def transition(job_id, to_state: JobState, *, actor: str, data: dict | None = None, **updates):
    """Única forma de mudar o estado de um Job.

    Bloqueia a linha (SELECT ... FOR UPDATE), valida a transição, aplica os
    campos em ``updates`` e grava um JobEvent — tudo na mesma transação.
    Lança ``InvalidTransition`` se a transição não for permitida.
    """
    with transaction.atomic():
        job = Job.objects.select_for_update().get(pk=job_id)
        from_state = job.state
        validate_transition(from_state, to_state)

        job.state = to_state
        for field, value in updates.items():
            setattr(job, field, value)
        if is_terminal(to_state):
            job.finished_at = timezone.now()
        job.save()

        JobEvent.objects.create(
            job=job, from_state=from_state, to_state=to_state, actor=actor, data=data or {}
        )

    log.info(
        "job.transition",
        job_id=str(job.id),
        from_state=from_state,
        to_state=str(to_state),
        actor=actor,
    )
    return job


def quote(job: Job, options: PrintOptions) -> dict:
    analysis = DocumentAnalysis.from_dict(job.analysis)
    return price_job(analysis, options, effective_price_rules(job.kiosk))


def update_options(job_id, raw_options: dict, *, actor: str) -> Job:
    """Altera as opções (só em ANALYZED) e recalcula o preço."""
    with transaction.atomic():
        job = Job.objects.select_for_update().get(pk=job_id)
        if job.state not in EDITABLE_STATES:
            raise InvalidTransition(job.state, job.state)
        options = PrintOptions.from_dict({**job.options, **raw_options})
        breakdown = quote(job, options)
        job.options = options.to_dict()
        job.price_breakdown = breakdown
        job.total_kz = breakdown["total_kz"]
        job.expires_at = session_deadline()
        job.save(
            update_fields=["options", "price_breakdown", "total_kz", "expires_at", "updated_at"]
        )
        JobEvent.objects.create(
            job=job,
            from_state=job.state,
            to_state=job.state,
            actor=actor,
            data={"options": job.options, "total_kz": job.total_kz},
        )
    log.info("job.options", job_id=str(job.id), total_kz=job.total_kz, actor=actor)
    return job


def checkout(job_id, *, actor: str, check_printer: bool = True) -> Job:
    """Cativa o valor na carteira e põe o trabalho na fila de impressão.

    Só avança se a impressora estiver pronta (PrinterNotReady). Tudo ou nada:
    se faltar saldo (InsufficientFunds) nada muda.
    """
    from . import printing, tasks

    if check_printer:
        job = Job.objects.select_related("kiosk__printer").get(pk=job_id)
        printing.ensure_ready(getattr(job.kiosk, "printer", None), allow_busy=True)
    with transaction.atomic():
        job = Job.objects.select_for_update().get(pk=job_id)
        validate_transition(job.state, JobState.RESERVED)
        wallet.place_hold(job, actor=actor)
        transition(job.pk, JobState.RESERVED, actor=actor, data={"held_kz": job.total_kz})
        job = transition(job.pk, JobState.QUEUED, actor=actor, expires_at=None)
        transaction.on_commit(lambda: tasks.print_job.delay(str(job.pk)))
    return job


def finish(job_id, to_state: JobState, *, actor: str, capture_kz: int = 0, **fields) -> Job:
    """Termina um trabalho e liquida a cativação (debita ``capture_kz``, liberta o resto)."""
    with transaction.atomic():
        job = Job.objects.select_for_update().get(pk=job_id)
        validate_transition(job.state, to_state)
        if job.state in FUNDS_HELD_STATES and WalletHold.objects.filter(job=job).exists():
            wallet.settle_hold(job, capture_kz, actor=actor)
        return transition(job.pk, to_state, actor=actor, **fields)


def cancel(job_id, *, actor: str) -> Job:
    return finish(job_id, JobState.CANCELLED, actor=actor)
