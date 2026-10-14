"""API do ecrã do kiosk.

Dois níveis de autenticação:
- ``Device <token>`` (o Pi): estado do kiosk, emparelhamento por QR, entrada por SMS;
- ``Kiosk <token de sessão>`` (o cliente identificado): perfil, ficheiros, trabalhos.
"""

from datetime import timedelta

import segno
import structlog
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone, translation
from rest_framework import serializers, status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from apps.accounts import otp
from apps.accounts.phone import normalize_phone
from apps.jobs.models import Document
from apps.jobs.previews import preview_urls
from apps.partners.services import effective_price_rules
from apps.wallets.services import wallet_for
from common.api import error
from common.auth import issue_kiosk_session
from domain.printing.blocking import block_reason

from .api import DeviceTokenAuthentication, IsDevice
from .models import KioskPairing, Printer

log = structlog.get_logger(__name__)
User = get_user_model()
PAIRING_TTL = timedelta(minutes=5)


def device_view(methods):
    def decorate(fn):
        fn = permission_classes([IsDevice])(fn)
        fn = authentication_classes([DeviceTokenAuthentication])(fn)
        return api_view(methods)(fn)

    return decorate


def _kiosk(request):
    kiosk = request.auth.kiosk
    if kiosk is None:
        raise serializers.ValidationError({"error": "device_without_kiosk"})
    return kiosk


def user_payload(user) -> dict:
    wallet = wallet_for(user)
    return {
        "name": user.full_name,
        "needs_name": not (user.full_name or "").strip(),
        "language": user.language,
        "available_kz": wallet.available_kz,
        "held_kz": wallet.held_kz,
    }


def session_payload(user, kiosk) -> dict:
    return {**issue_kiosk_session(user, kiosk), "user": user_payload(user)}


def client_url(request, path: str) -> str:
    host = settings.HOST_CLIENT or request.get_host()
    scheme = "https" if settings.HOST_CLIENT else request.scheme
    return f"{scheme}://{host}{path}"


# --- estado do kiosk ----------------------------------------------------------


FRESH_SECONDS = 15


def fresh_printer(printer: Printer) -> Printer:
    """Lê a impressora na hora se a última leitura tiver mais de 15 s (um pedido de cada vez)."""
    from apps.monitoring.services import poll_printer

    stale = printer.last_polled_at is None or (
        timezone.now() - printer.last_polled_at
    ).total_seconds() > FRESH_SECONDS
    if stale and cache.add(f"printer_poll:{printer.pk}", 1, FRESH_SECONDS):
        try:
            return poll_printer(printer)
        except Exception:  # a monitorização regular volta a tentar
            log.exception("kiosk.printer_poll_failed", printer=printer.name)
    return printer


@device_view(["GET"])
def kiosk_state(request):
    """O ecrã consulta a cada ~10 s: pode atender clientes? Se não, porquê?"""
    kiosk = _kiosk(request)
    printer: Printer = fresh_printer(request.auth.printer)
    if not kiosk.is_active or kiosk.status in ("MAINTENANCE", "OUT_OF_SERVICE"):
        reason = "maintenance"
    else:
        reason = block_reason(printer.status, printer.status_detail) or ""
    available = not reason
    rules = effective_price_rules(kiosk)
    return Response(
        {
            "kiosk": {"code": kiosk.code, "name": kiosk.name, "site": kiosk.site.name},
            "available": available,
            "reason": reason,
            "printer": {
                "status": printer.status,
                "color": printer.supports_color,
                "duplex": printer.supports_duplex,
            },
            "prices": {
                "bw_page_kz": rules.bw_page_kz,
                "color_from_kz": rules.color_low_kz,
                "min_transaction_kz": rules.min_transaction_kz,
            },
            "portal_url": client_url(request, "/"),
            "server_time": timezone.now(),
        }
    )


# --- emparelhamento por QR --------------------------------------------------------


def qr_svg(data: str, *, upper: bool = False) -> str:
    """QR para o ecrã (não se estraga como papel: correção L = quadrados maiores).

    ``upper``: endereço em maiúsculas cabe no modo alfanumérico do QR (25×25 em vez
    de 33×33); só para caminhos que aceitam maiúsculas (o emparelhamento /K/).
    """
    if upper:
        data = data.upper()
    return segno.make(data, error="l").svg_inline(scale=8, border=4, dark="#0b1724")


@device_view(["POST"])
def create_pairing(request):
    kiosk = _kiosk(request)
    KioskPairing.objects.filter(kiosk=kiosk, status=KioskPairing.Status.PENDING).update(
        status=KioskPairing.Status.EXPIRED
    )
    pairing = KioskPairing.objects.create(kiosk=kiosk, expires_at=timezone.now() + PAIRING_TTL)
    url = client_url(request, f"/k/{pairing.code}/")
    return Response(
        {
            "id": str(pairing.id),
            "code": pairing.code,
            "url": url,
            "qr_svg": qr_svg(url, upper=True),
            "expires_at": pairing.expires_at,
        },
        status=status.HTTP_201_CREATED,
    )


@device_view(["GET"])
def pairing_status(request, pairing_id):
    """Quando o telemóvel confirma, devolve (uma única vez) a sessão do cliente."""
    kiosk = _kiosk(request)
    with transaction.atomic():
        pairing = (
            KioskPairing.objects.select_for_update(of=("self",))
            .filter(pk=pairing_id, kiosk=kiosk)
            .select_related("user")
            .first()
        )
        if pairing is None:
            return error("not_found", "Unknown pairing", 404)
        if pairing.status == KioskPairing.Status.PENDING and not pairing.is_open:
            pairing.status = KioskPairing.Status.EXPIRED
            pairing.save(update_fields=["status"])
        if pairing.status != KioskPairing.Status.CLAIMED:
            return Response({"status": pairing.status})
        pairing.status = KioskPairing.Status.CONSUMED
        pairing.save(update_fields=["status"])
    log.info("kiosk.paired", kiosk=kiosk.code, user_id=pairing.user_id)
    return Response({"status": "CLAIMED", **session_payload(pairing.user, kiosk)})


@device_view(["GET"])
def scanner_state(request):
    """Estado do digitalizador para o ecrã de cópia (há papel no alimentador?)."""
    from apps.jobs.copying import scanner_for
    from domain.scanning.gateway import ScannerUnavailable

    _kiosk(request)
    try:
        state = scanner_for(request.auth.printer).status()
    except ScannerUnavailable as exc:
        return Response({"available": False, "error": exc.code})
    return Response({"available": True, **state.to_dict()})


@device_view(["GET"])
def upload_qr(request):
    """QR que leva o telemóvel a "Meus ficheiros" do portal, para enviar na hora."""
    url = client_url(request, "/ficheiros/")
    return Response({"url": url, "qr_svg": qr_svg(url)})


# --- entrada com número + SMS no próprio kiosk ---------------------------------------


class PhoneSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=20)
    language = serializers.ChoiceField(choices=[c for c, _ in settings.LANGUAGES], default="pt")

    def validate_phone(self, value):
        try:
            return normalize_phone(value)
        except Exception as exc:
            raise serializers.ValidationError("invalid_phone") from exc


class VerifySerializer(PhoneSerializer):
    code = serializers.RegexField(r"^\d{6}$")


@device_view(["POST"])
def login_request(request):
    kiosk = _kiosk(request)
    payload = PhoneSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    try:
        _phone, code = otp.request_code(
            payload.validated_data["phone"],
            ip=f"kiosk:{kiosk.pk}",
            language=payload.validated_data["language"],
        )
    except otp.OtpError as exc:
        return error(exc.code, str(exc), 429 if exc.code.startswith("too") else 502)
    body = {"sent": True}
    if code:
        body["dev_code"] = code  # só em modo de desenvolvimento (OTP_SHOW_CODE)
    return Response(body)


@device_view(["POST"])
def login_verify(request):
    kiosk = _kiosk(request)
    payload = VerifySerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    phone = payload.validated_data["phone"]
    try:
        ok = otp.verify_code(phone, payload.validated_data["code"])
    except otp.OtpError as exc:
        return error(exc.code, str(exc), 400)
    if not ok:
        return error("wrong_code", "Wrong code", 400)
    user = User.objects.filter(phone=phone).first()
    if user is None:
        user = User.objects.create_user(
            phone,
            language=payload.validated_data["language"],
            phone_verified_at=timezone.now(),
        )
    elif not user.is_active:
        return error("account_disabled", "This account is disabled", 403)
    log.info("kiosk.sms_login", kiosk=kiosk.code, user_id=user.pk)
    return Response(session_payload(user, kiosk))


# --- cliente identificado (sessão do kiosk) ------------------------------------------


class NameSerializer(serializers.Serializer):
    full_name = serializers.CharField(min_length=2, max_length=150)


@api_view(["GET", "POST"])
def me(request):
    if request.method == "POST":
        payload = NameSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        name = " ".join(payload.validated_data["full_name"].split())
        if len(name) < 2:
            return error("invalid_name", "Please enter your name", 400)
        request.user.full_name = name
        request.user.save(update_fields=["full_name"])
    with translation.override(request.user.language):
        return Response(user_payload(request.user))


@api_view(["GET"])
def documents(request):
    docs = request.user.documents.filter(expires_at__gt=timezone.now()).exclude(
        state=Document.State.REJECTED
    )[:50]
    return Response(
        [
            {
                "id": str(d.id),
                "filename": d.original_filename,
                "source": d.source,
                "email_from": d.email_from,
                "state": d.state,
                "pages": d.pages,
                "color_pages": d.color_pages,
                "created_at": d.created_at,
                "preview_urls": preview_urls(d),
            }
            for d in docs
        ]
    )
