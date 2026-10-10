from .base import *  # noqa: F401,F403
from .base import env

DEBUG = False
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=3600)
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# Modo temporário de desenvolvimento: acesso por http://<IP>:8100 sem HTTPS.
# Desligar (remover do .env) assim que o domínio tiver certificado.
if env.bool("DJANGO_ALLOW_INSECURE_HTTP", default=False):
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False
    SECURE_HSTS_SECONDS = 0
