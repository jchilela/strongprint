"""API v1 — carteira do cliente e webhooks de pagamento."""

from django.conf import settings
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from rest_framework import serializers, status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.wallets.models import LedgerEntry
from apps.wallets.services import wallet_for
from common.api import error
from domain.payments.provider import InvalidWebhook

from . import services
from .models import TopUp

PROVIDERS = {"strongpay", "mock"}


class TopUpSerializer(serializers.ModelSerializer):
    class Meta:
        model = TopUp
        fields = [
            "id",
            "amount_kz",
            "status",
            "provider",
            "entity",
            "reference",
            "expires_at",
            "paid_amount_kz",
            "paid_at",
            "created_at",
        ]


class LedgerEntrySerializer(serializers.ModelSerializer):
    kiosk = serializers.CharField(source="kiosk.code", default=None)

    class Meta:
        model = LedgerEntry
        fields = [
            "kind",
            "amount_kz",
            "balance_after_kz",
            "kiosk",
            "job",
            "reference",
            "created_at",
        ]


class CreateTopUpSerializer(serializers.Serializer):
    amount_kz = serializers.IntegerField()

    def validate_amount_kz(self, value):
        if not settings.TOPUP_MIN_KZ <= value <= settings.TOPUP_MAX_KZ:
            raise serializers.ValidationError(
                f"Between {settings.TOPUP_MIN_KZ} and {settings.TOPUP_MAX_KZ} Kz"
            )
        return value


def topup_for_request(request, topup_id) -> TopUp:
    qs = TopUp.objects.all()
    if not request.user.is_staff:
        qs = qs.filter(wallet__user=request.user)
    return get_object_or_404(qs, pk=topup_id)


@api_view(["GET"])
def wallet_detail(request):
    wallet = wallet_for(request.user)
    entries = wallet.entries.select_related("kiosk")[:20]
    pending = wallet.topups.filter(status=TopUp.Status.PENDING)[:5]
    return Response(
        {
            "balance_kz": wallet.balance_kz,
            "held_kz": wallet.held_kz,
            "available_kz": wallet.available_kz,
            "entries": LedgerEntrySerializer(entries, many=True).data,
            "pending_topups": TopUpSerializer(pending, many=True).data,
        }
    )


@api_view(["POST"])
def create_topup(request):
    payload = CreateTopUpSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    topup = services.create_topup(
        request.user,
        payload.validated_data["amount_kz"],
        actor=f"user:{request.user.pk}",
        idempotency_key=request.headers.get("Idempotency-Key"),
    )
    return Response(TopUpSerializer(topup).data, status=status.HTTP_201_CREATED)


@api_view(["GET"])
def topup_detail(request, topup_id):
    return Response(TopUpSerializer(topup_for_request(request, topup_id)).data)


@api_view(["POST"])
def simulate_topup(request, topup_id):
    """Desenvolvimento: marca um carregamento mock como pago (ou falhado)."""
    topup = topup_for_request(request, topup_id)
    new_status = request.data.get("status", "paid")
    if new_status not in {"paid", "failed", "cancelled", "expired"}:
        return error("invalid_status", "Use paid, failed, cancelled or expired", 400)
    try:
        topup = services.simulate_payment(topup, new_status)
    except PermissionError as exc:
        return error("simulation_disabled", str(exc), 403)
    return Response(TopUpSerializer(topup).data)


@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def payment_webhook(request, provider):
    """Recebe ``payment.updated`` do StrongPay (ou do mock). Idempotente."""
    if provider not in PROVIDERS:
        return error("unknown_provider", provider, 404)
    try:
        result, topup = services.handle_webhook(provider, dict(request.headers), request.body)
    except InvalidWebhook as exc:
        return error("invalid_webhook", str(exc), 400)
    return Response({"success": True, "result": result, "topup": str(topup.id) if topup else None})
