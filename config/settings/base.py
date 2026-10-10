from pathlib import Path

import environ
from django.utils.translation import gettext_lazy as _

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env", overwrite=False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

# Subdomínios (vazios em desenvolvimento = sem restrições). Ver common/hosts.py.
HOST_CLIENT = env("HOST_CLIENT", default="")
HOST_ADMIN = env("HOST_ADMIN", default="")
HOST_API = env("HOST_API", default="")
HOST_APP = env("HOST_APP", default="")
HOST_PARTNER = env("HOST_PARTNER", default="")
HOST_ROOTS = env.list("HOST_ROOTS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "apps.accounts",
    "apps.partners",
    "apps.devices",
    "apps.wallets",
    "apps.jobs",
    "apps.payments",
    "apps.monitoring",
    "apps.portal",
    "apps.partner_portal",
]

MIDDLEWARE = [
    "common.middleware.LogContextMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "common.hosts.HostRoutingMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "apps.portal.middleware.NameRequiredMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
                "apps.portal.context.portal",
            ],
        },
    },
]

DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["ATOMIC_REQUESTS"] = False
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

# Internacionalização: português (por omissão) e inglês.
LANGUAGE_CODE = "pt"
LANGUAGES = [("pt", _("Portuguese")), ("en", _("English"))]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "Africa/Luanda"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

REDIS_URL = env("REDIS_URL")

# Celery
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = None
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TIMEZONE = TIME_ZONE
CELERY_WORKER_HIJACK_ROOT_LOGGER = False

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "common.auth.KioskSessionAuthentication",
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.BasicAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "EXCEPTION_HANDLER": "common.api.exception_handler",
    "UNAUTHENTICATED_USER": "django.contrib.auth.models.AnonymousUser",
}

# Armazenamento de ficheiros (S3; SeaweedFS em dev)
S3_ENDPOINT_URL = env("S3_ENDPOINT_URL", default=None)
S3_PUBLIC_ENDPOINT_URL = env("S3_PUBLIC_ENDPOINT_URL", default=None)
S3_ACCESS_KEY_ID = env("S3_ACCESS_KEY_ID", default="")
S3_SECRET_ACCESS_KEY = env("S3_SECRET_ACCESS_KEY", default="")
S3_BUCKET = env("S3_BUCKET", default="strongprint-jobs")
S3_REGION = env("S3_REGION", default="us-east-1")

# Regras de negócio StrongPrint
PRINTER_GATEWAY = env("PRINTER_GATEWAY", default="fake")
JOB_SESSION_TIMEOUT_MINUTES = env.int("JOB_SESSION_TIMEOUT_MINUTES", default=10)
FILE_RETENTION_HOURS = env.int("FILE_RETENTION_HOURS", default=24)
KIOSK_SESSION_MINUTES = env.int("KIOSK_SESSION_MINUTES", default=15)
UPLOAD_MAX_MB = env.int("UPLOAD_MAX_MB", default=50)
MAX_DOCUMENT_PAGES = env.int("MAX_DOCUMENT_PAGES", default=300)
# Página é a cores quando pelo menos esta % de píxeis tem cor real.
ANALYSIS_COLOR_THRESHOLD_PCT = env.float("ANALYSIS_COLOR_THRESHOLD_PCT", default=0.1)
PRINT_QUEUE_TIMEOUT_MINUTES = env.int("PRINT_QUEUE_TIMEOUT_MINUTES", default=15)
PRINTER_TIMEOUT_SECONDS = env.int("PRINTER_TIMEOUT_SECONDS", default=10)
PRINT_POLL_SECONDS = env.float("PRINT_POLL_SECONDS", default=3)
PRINT_COUNTER_SETTLE_SECONDS = env.float("PRINT_COUNTER_SETTLE_SECONDS", default=8)
PRINT_JOB_TIMEOUT_SECONDS = env.int("PRINT_JOB_TIMEOUT_SECONDS", default=900)
PRINT_RETRY_SECONDS = env.int("PRINT_RETRY_SECONDS", default=30)
# Monitorização
KIOSK_OFFLINE_AFTER_SECONDS = env.int("KIOSK_OFFLINE_AFTER_SECONDS", default=90)
TONER_LOW_PCT = env.int("TONER_LOW_PCT", default=10)
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

LIBRARY_RETENTION_DAYS = env.int("LIBRARY_RETENTION_DAYS", default=7)

# Códigos de acesso por telefone: console (log; desenvolvimento) | strongx (SMS) | http (genérico)
OTP_SENDER = env("OTP_SENDER", default="console")
STRONGX_BASE_URL = env("STRONGX_BASE_URL", default="https://api.strongx.it.ao/v1")
STRONGX_API_KEY = env("STRONGX_API_KEY", default="")
STRONGX_APPLICATION_ID = env("STRONGX_APPLICATION_ID", default="")
OTP_HTTP_URL = env("OTP_HTTP_URL", default="")
OTP_HTTP_TOKEN = env("OTP_HTTP_TOKEN", default="")
# Mostra o código no ecrã — SÓ em desenvolvimento, enquanto não há envio real.
OTP_SHOW_CODE = env.bool("OTP_SHOW_CODE", default=DEBUG)
OTP_TTL_MINUTES = env.int("OTP_TTL_MINUTES", default=10)
OTP_MAX_ATTEMPTS = env.int("OTP_MAX_ATTEMPTS", default=5)

LOGIN_URL = "portal:login"
LOGIN_REDIRECT_URL = "portal:home"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 30

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
        "KEY_PREFIX": "strongprint",
    }
}

# Pagamentos (carregamento da carteira): mock | strongpay
PAYMENT_PROVIDER = env("PAYMENT_PROVIDER", default="mock")
PAYMENTS_ALLOW_SIMULATION = env.bool("PAYMENTS_ALLOW_SIMULATION", default=True)
MOCK_PAYMENT_SECRET = env("MOCK_PAYMENT_SECRET", default="mock-secret")
STRONGPAY_BASE_URL = env("STRONGPAY_BASE_URL", default="https://strongpay.strongbox.ao")
STRONGPAY_API_KEY = env("STRONGPAY_API_KEY", default="")
STRONGPAY_WEBHOOK_SECRET = env("STRONGPAY_WEBHOOK_SECRET", default="")
TOPUP_MIN_KZ = env.int("TOPUP_MIN_KZ", default=100)
TOPUP_MAX_KZ = env.int("TOPUP_MAX_KZ", default=500_000)

from common.logging import configure_logging  # noqa: E402

LOGGING_CONFIG = None
configure_logging(debug=DEBUG)
