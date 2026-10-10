import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("strongprint")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Agendamentos de beat são acrescentados nas fases C (expiração) e E (monitorização).
app.conf.beat_schedule = {}
