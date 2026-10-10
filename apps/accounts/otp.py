"""Códigos de acesso de uso único (registo e entrada por telefone).

- 6 dígitos, válidos ``OTP_TTL_MINUTES``, no máximo ``OTP_MAX_ATTEMPTS`` tentativas;
- limites de envio por telefone (1/min, 5/h) e por IP (20/h);
- envio por ``OTP_SENDER``: ``console`` (log; desenvolvimento) ou ``http``
  (POST JSON para um gateway de SMS/WhatsApp configurável).
"""

import hashlib
import hmac
import secrets
from datetime import timedelta

import httpx
import structlog
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from .models import PhoneOTP
from .phone import normalize_phone

log = structlog.get_logger(__name__)


class OtpError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _hash(phone: str, code: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode(), f"{phone}:{code}".encode(), hashlib.sha256
    ).hexdigest()


def _hit(key: str, limit: int, window: int) -> bool:
    """Incrementa um contador com janela; devolve False se ultrapassou o limite."""
    added = cache.add(key, 1, window)
    if added:
        return True
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, window)
        return True
    return count <= limit


def message_for(code: str, language: str = "pt") -> str:
    if language == "en":
        return f"StrongPrint: your code is {code}. It expires in {settings.OTP_TTL_MINUTES} min."
    return f"StrongPrint: o seu código é {code}. Válido por {settings.OTP_TTL_MINUTES} min."


def send(phone: str, message: str) -> None:
    sender = settings.OTP_SENDER
    if sender == "http":
        try:
            response = httpx.post(
                settings.OTP_HTTP_URL,
                json={"to": phone, "message": message},
                headers={"Authorization": f"Bearer {settings.OTP_HTTP_TOKEN}"},
                timeout=10,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.error("otp.send_failed", phone=phone, reason=str(exc))
            raise OtpError("send_failed", "Could not send the code") from exc
        return
    log.info("otp.console", phone=phone, message=message)


def request_code(raw_phone: str, *, ip: str = "", language: str = "pt") -> tuple[str, str | None]:
    """Gera e envia um código. Devolve (telefone normalizado, código se OTP_SHOW_CODE)."""
    phone = normalize_phone(raw_phone)
    if not _hit(f"otp:min:{phone}", 1, 60):
        raise OtpError("too_soon", "Wait a minute before asking for a new code")
    if not _hit(f"otp:hour:{phone}", 5, 3600) or (ip and not _hit(f"otp:ip:{ip}", 20, 3600)):
        raise OtpError("too_many", "Too many codes requested. Try again later")

    code = f"{secrets.randbelow(10**6):06d}"
    PhoneOTP.objects.filter(phone=phone, used_at__isnull=True).update(used_at=timezone.now())
    PhoneOTP.objects.create(
        phone=phone,
        code_hash=_hash(phone, code),
        channel=settings.OTP_SENDER,
        expires_at=timezone.now() + timedelta(minutes=settings.OTP_TTL_MINUTES),
    )
    send(phone, message_for(code, language))
    return phone, (code if settings.OTP_SHOW_CODE else None)


def verify_code(phone: str, code: str) -> bool:
    otp = (
        PhoneOTP.objects.filter(phone=phone, used_at__isnull=True, expires_at__gt=timezone.now())
        .order_by("-created_at")
        .first()
    )
    if otp is None:
        raise OtpError("expired", "The code expired. Ask for a new one")
    if otp.attempts >= settings.OTP_MAX_ATTEMPTS:
        raise OtpError("too_many_attempts", "Too many attempts. Ask for a new code")
    otp.attempts += 1
    ok = hmac.compare_digest(otp.code_hash, _hash(phone, (code or "").strip()))
    if ok:
        otp.used_at = timezone.now()
    otp.save(update_fields=["attempts", "used_at"])
    return ok
