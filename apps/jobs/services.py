import structlog
from django.db import transaction
from django.utils import timezone

from domain.jobs.state_machine import JobState, is_terminal, validate_transition

from .models import Job, JobEvent

log = structlog.get_logger(__name__)


def create_job(*, kiosk, customer, actor: str, **fields) -> Job:
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
