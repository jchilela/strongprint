"""Sessões de kiosk: token assinado e de curta duração que liga um cliente a um kiosk.

Fluxo: o cliente autenticado (portal/app) pede ``POST /api/v1/kiosks/{code}/sessions``
— no futuro ao ler o QR do ecrã — e o ecrã do kiosk usa o token devolvido em
``Authorization: Kiosk <token>``. Só o kiosk dessa sessão pode ser usado com ele.
"""

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.utils import timezone
from rest_framework import authentication, exceptions

SALT = "strongprint.kiosk-session"
KEYWORD = "Kiosk"


@dataclass(frozen=True)
class KioskSession:
    kiosk_id: int
    user_id: int


def issue_kiosk_session(user, kiosk) -> dict:
    token = signing.dumps({"k": kiosk.pk, "u": user.pk}, salt=SALT, compress=True)
    expires = timezone.now() + timedelta(minutes=settings.KIOSK_SESSION_MINUTES)
    return {"token": token, "token_type": KEYWORD, "expires_at": expires.isoformat()}


def read_kiosk_session(token: str) -> KioskSession:
    try:
        data = signing.loads(token, salt=SALT, max_age=settings.KIOSK_SESSION_MINUTES * 60)
    except signing.SignatureExpired as exc:
        raise exceptions.AuthenticationFailed("Kiosk session expired") from exc
    except signing.BadSignature as exc:
        raise exceptions.AuthenticationFailed("Invalid kiosk session") from exc
    return KioskSession(kiosk_id=int(data["k"]), user_id=int(data["u"]))


class KioskSessionAuthentication(authentication.BaseAuthentication):
    def authenticate(self, request):
        header = authentication.get_authorization_header(request).split()
        if not header or header[0].decode().lower() != KEYWORD.lower():
            return None
        if len(header) != 2:
            raise exceptions.AuthenticationFailed("Invalid kiosk session header")
        session = read_kiosk_session(header[1].decode())
        user = get_user_model().objects.filter(pk=session.user_id, is_active=True).first()
        if user is None:
            raise exceptions.AuthenticationFailed("Unknown user")
        return user, session

    def authenticate_header(self, request):
        return KEYWORD


def session_kiosk_id(request) -> int | None:
    """Kiosk ao qual o pedido está preso (None se não for sessão de kiosk)."""
    auth = getattr(request, "auth", None)
    return auth.kiosk_id if isinstance(auth, KioskSession) else None
