import os

import structlog
from celery import Celery
from celery.signals import task_prerun

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("strongprint")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.beat_schedule = {
    "expire-jobs": {"task": "apps.jobs.tasks.expire_jobs", "schedule": 60.0},
    "fail-stale-queued-jobs": {"task": "apps.jobs.tasks.fail_stale_queued_jobs", "schedule": 60.0},
    "purge-job-files": {"task": "apps.jobs.tasks.purge_job_files", "schedule": 1800.0},
    "reconcile-topups": {"task": "apps.payments.tasks.reconcile_topups", "schedule": 120.0},
}


@task_prerun.connect
def _reset_log_context(**_kwargs):
    # Cada tarefa começa sem o job_id da anterior.
    structlog.contextvars.clear_contextvars()
