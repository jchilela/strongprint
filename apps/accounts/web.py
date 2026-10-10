"""Entrada por telefone + código, partilhada pelo portal do cliente e pelo do parceiro."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.shortcuts import redirect
from django.utils import timezone, translation
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _

from . import otp


def client_ip(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return forwarded.split(",")[0].strip() or request.META.get("REMOTE_ADDR", "")


def safe_next(request, url: str | None) -> str | None:
    if url and url_has_allowed_host_and_scheme(url, {request.get_host()}):
        return url
    return None


def otp_message(exc: otp.OtpError) -> str:
    return {
        "too_soon": _("Please wait a minute before asking for a new code."),
        "too_many": _("Too many codes requested. Try again later."),
        "expired": _("The code expired. Ask for a new one."),
        "too_many_attempts": _("Too many attempts. Ask for a new code."),
        "send_failed": _("We could not send the code. Try again."),
    }.get(exc.code, str(exc))


def send_code(request, phone: str) -> str:
    """Envia o código; em desenvolvimento mostra-o numa mensagem. Lança OtpError."""
    phone, code = otp.request_code(
        phone, ip=client_ip(request), language=translation.get_language() or "pt"
    )
    if code:
        messages.info(request, _("Development mode — your code is %(code)s") % {"code": code})
    return phone


def check_code(phone: str, code: str) -> str | None:
    """None se o código estiver certo; caso contrário a mensagem de erro."""
    try:
        ok = otp.verify_code(phone, code)
    except otp.OtpError as exc:
        return otp_message(exc)
    return None if ok else _("Wrong code. Try again.")


def sign_in(request, user, redirect_to: str):
    if user.phone_verified_at is None:
        user.phone_verified_at = timezone.now()
        user.save(update_fields=["phone_verified_at"])
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    translation.activate(user.language)
    response = redirect(redirect_to)
    response.set_cookie(settings.LANGUAGE_COOKIE_NAME, user.language, max_age=365 * 86400)
    return response
