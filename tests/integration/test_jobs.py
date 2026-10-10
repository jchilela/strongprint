import pytest

from apps.jobs.models import JobEvent
from apps.jobs.services import create_job, transition
from domain.jobs.state_machine import InvalidTransition, JobState
from tests.factories import JobFactory, KioskFactory, UserFactory

pytestmark = pytest.mark.django_db


def test_create_job_records_initial_event():
    job = create_job(kiosk=KioskFactory(), customer=UserFactory(), actor="test")
    events = list(job.events.all())
    assert job.state == JobState.CREATED
    assert job.options == {"color": False, "duplex": False, "copies": 1, "page_range": None}
    assert [(e.from_state, e.to_state) for e in events] == [("", "CREATED")]


def test_transition_updates_state_fields_and_audit():
    job = JobFactory()
    job = transition(
        job.pk,
        JobState.FILE_RECEIVED,
        actor="kiosk:1",
        data={"size": 1234},
        file_key="jobs/x.pdf",
        file_size=1234,
    )
    job.refresh_from_db()
    assert job.state == JobState.FILE_RECEIVED
    assert job.file_key == "jobs/x.pdf"
    event = JobEvent.objects.get(job=job)
    assert (event.from_state, event.to_state, event.actor, event.data) == (
        "CREATED",
        "FILE_RECEIVED",
        "kiosk:1",
        {"size": 1234},
    )
    assert job.finished_at is None


def test_invalid_transition_changes_nothing():
    job = JobFactory()
    with pytest.raises(InvalidTransition):
        transition(job.pk, JobState.PRINTING, actor="test", pages=5)
    job.refresh_from_db()
    assert job.state == JobState.CREATED
    assert job.pages is None
    assert not JobEvent.objects.filter(job=job).exists()


def test_terminal_transition_sets_finished_at():
    job = JobFactory()
    job = transition(job.pk, JobState.CANCELLED, actor="user:1")
    assert job.finished_at is not None


def test_full_lifecycle_audit_trail():
    job = JobFactory()
    path = [
        JobState.FILE_RECEIVED,
        JobState.ANALYZED,
        JobState.RESERVED,
        JobState.QUEUED,
        JobState.PRINTING,
        JobState.PARTIAL,
    ]
    for state in path:
        transition(job.pk, state, actor="test")
    trail = list(job.events.values_list("to_state", flat=True))
    assert trail == [str(s) for s in path]
