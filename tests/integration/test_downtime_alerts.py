from datetime import timedelta

import pytest
from django.core import mail
from django.core.cache import cache
from django.utils import timezone

from apps.monitoring.models import Downtime
from apps.monitoring.notify import notify_downtimes

pytestmark = pytest.mark.django_db
R = Downtime.Reason


@pytest.fixture(autouse=True)
def alerts(settings):
    settings.ALERT_EMAILS = ["owner@example.com"]
    settings.DOWNTIME_ALERT_AFTER_SECONDS = 180
    settings.DOWNTIME_ALERT_MAX_PER_HOUR = 6
    settings.HOST_ADMIN = "admin.example.com"
    cache.clear()


def down(kiosk, reason, *, ago, ended_ago=None, **kwargs):
    now = timezone.now()
    return Downtime.objects.create(
        kiosk=kiosk,
        printer=kiosk.printer,
        reason=reason,
        started_at=now - timedelta(minutes=ago),
        ended_at=now - timedelta(minutes=ended_ago) if ended_ago is not None else None,
        **kwargs,
    )


def test_waits_before_alerting(kiosk):
    down(kiosk, R.PAPER_OUT, ago=1)
    assert notify_downtimes() == 0
    assert mail.outbox == []


def test_alert_then_recovery(kiosk):
    downtime = down(kiosk, R.PAPER_OUT, ago=5, detail={"reasons": ["media-empty-error"]})
    assert notify_downtimes() == 1
    message = mail.outbox[0]
    assert message.to == ["owner@example.com"]
    assert message.subject == f"⚠️ Kiosk {kiosk.code} parado — Sem papel"
    assert "media-empty-error" in message.body
    assert f"kiosk__id__exact={kiosk.pk}" in message.body
    assert notify_downtimes() == 0  # não repete

    Downtime.objects.filter(pk=downtime.pk).update(ended_at=timezone.now())
    assert notify_downtimes() == 1
    assert mail.outbox[1].subject.startswith(
        f"✅ Kiosk {kiosk.code} voltou a funcionar (parado 5 min"
    )
    assert notify_downtimes() == 0


def test_short_problem_never_emails(kiosk):
    down(kiosk, R.OFFLINE, ago=2, ended_ago=1)
    assert notify_downtimes() == 0
    assert mail.outbox == []


def test_reason_change_emails_again_and_one_recovery(kiosk):
    now = timezone.now()
    first = down(kiosk, R.PAPER_OUT, ago=20, notified_at=now - timedelta(minutes=17))
    Downtime.objects.filter(pk=first.pk).update(
        started_at=now - timedelta(minutes=20), ended_at=now - timedelta(minutes=10)
    )
    second = down(kiosk, R.PAPER_JAM, ago=10)
    Downtime.objects.filter(pk=second.pk).update(started_at=now - timedelta(minutes=10))
    assert notify_downtimes() == 1
    assert "Papel encravado" in mail.outbox[0].subject
    assert "há 20 min" in mail.outbox[0].body  # conta desde o início da paragem

    Downtime.objects.filter(pk=second.pk).update(ended_at=now)
    assert notify_downtimes() == 1
    assert "parado 20 min" in mail.outbox[1].subject
    assert notify_downtimes() == 0
    assert len(mail.outbox) == 2


def test_recovery_waits_while_new_problem_is_recent(kiosk):
    now = timezone.now()
    first = down(kiosk, R.PAPER_OUT, ago=10, notified_at=now - timedelta(minutes=7))
    Downtime.objects.filter(pk=first.pk).update(ended_at=now - timedelta(minutes=1))
    second = down(kiosk, R.ERROR, ago=1)
    assert notify_downtimes() == 0
    Downtime.objects.filter(pk=second.pk).update(ended_at=now)
    assert notify_downtimes() == 1
    assert "voltou" in mail.outbox[0].subject


def test_maintenance_is_silent(kiosk):
    now = timezone.now()
    down(kiosk, R.MAINTENANCE, ago=30)
    assert notify_downtimes() == 0
    Downtime.objects.all().delete()
    problem = down(kiosk, R.PAPER_OUT, ago=10, notified_at=now - timedelta(minutes=7))
    Downtime.objects.filter(pk=problem.pk).update(ended_at=now - timedelta(minutes=1))
    down(kiosk, R.MAINTENANCE, ago=1)
    assert notify_downtimes() == 0
    problem.refresh_from_db()
    assert problem.recovery_notified_at is not None


def test_rate_limit(kiosk, settings):
    settings.DOWNTIME_ALERT_MAX_PER_HOUR = 2
    for _ in range(3):
        Downtime.objects.filter(ended_at__isnull=True).update(ended_at=timezone.now())
        down(kiosk, R.ERROR, ago=5)
        notify_downtimes()
    assert len([m for m in mail.outbox if "parado —" in m.subject]) <= 2


def test_disabled_without_recipients(kiosk, settings):
    settings.ALERT_EMAILS = []
    down(kiosk, R.PAPER_OUT, ago=10)
    assert notify_downtimes() == 0


def test_smtp_failure_retries(kiosk, monkeypatch):
    import apps.monitoring.notify as notify

    downtime = down(kiosk, R.PAPER_OUT, ago=5)

    def boom(*args, **kwargs):
        raise OSError("smtp down")

    monkeypatch.setattr(notify, "send_mail", boom)
    assert notify_downtimes() == 0
    downtime.refresh_from_db()
    assert downtime.notified_at is None
    monkeypatch.undo()
    assert notify_downtimes() == 1
