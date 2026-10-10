import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgres://strongprint:strongprint@db:5432/strongprint")
os.environ.setdefault("REDIS_URL", "redis://redis:6379/15")

from .base import *  # noqa: E402,F401,F403

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
PRINTER_GATEWAY = "fake"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
