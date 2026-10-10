"""API do Raspberry Pi (autenticada pelo token do dispositivo)."""

import structlog
from django.conf import settings
from django.utils import timezone
from rest_framework import serializers
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from apps.monitoring.models import Alert
from apps.monitoring.services import resolve_alert
from apps.partners.models import Kiosk

from .models import EdgeDevice

log = structlog.get_logger(__name__)


class DeviceTokenAuthentication(BaseAuthentication):
    """``Authorization: Device <token>`` → request.auth = EdgeDevice."""

    keyword = "device"

    def authenticate(self, request):
        header = get_authorization_header(request).split()
        if not header or header[0].decode().lower() != self.keyword:
            return None
        if len(header) != 2:
            raise AuthenticationFailed("Invalid device header")
        device = EdgeDevice.authenticate(header[1].decode())
        if device is None:
            raise AuthenticationFailed("Unknown or inactive device")
        return None, device

    def authenticate_header(self, request):
        return "Device"


class IsDevice(BasePermission):
    def has_permission(self, request, view):
        return isinstance(request.auth, EdgeDevice)


class HeartbeatSerializer(serializers.Serializer):
    version = serializers.CharField(max_length=40, required=False, allow_blank=True)
    vpn_ip = serializers.IPAddressField(required=False, allow_null=True)
    uptime_seconds = serializers.IntegerField(required=False, min_value=0)
    usb_present = serializers.BooleanField(required=False)


@api_view(["POST"])
@authentication_classes([DeviceTokenAuthentication])
@permission_classes([IsDevice])
def heartbeat(request):
    """O Pi chama a cada ~30 s. A resposta pode trazer comandos (reload, reboot, out_of_service)."""
    device: EdgeDevice = request.auth
    payload = HeartbeatSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    data = payload.validated_data

    device.last_heartbeat = timezone.now()
    fields = ["last_heartbeat", "updated_at"]
    if data.get("version"):
        device.version = data["version"]
        fields.append("version")
    if data.get("vpn_ip"):
        device.vpn_ip = data["vpn_ip"]
        fields.append("vpn_ip")
    commands = [device.pending_command] if device.pending_command else []
    if device.pending_command:
        device.pending_command = ""
        fields.append("pending_command")
    device.save(update_fields=fields)

    kiosk = device.kiosk
    if kiosk is None:
        return Response({"kiosk": None, "commands": commands, "server_time": timezone.now()})
    if kiosk.status == Kiosk.Status.OFFLINE:
        Kiosk.objects.filter(pk=kiosk.pk).update(status=Kiosk.Status.ONLINE)
        kiosk.status = Kiosk.Status.ONLINE
        log.info("kiosk.online", kiosk=kiosk.code)
    resolve_alert(Alert.Kind.KIOSK_OFFLINE, kiosk)
    if kiosk.status in (Kiosk.Status.OUT_OF_SERVICE, Kiosk.Status.MAINTENANCE) or (
        not kiosk.is_active
    ):
        commands.append("out_of_service")

    printer = device.printer
    return Response(
        {
            "kiosk": {"code": kiosk.code, "name": kiosk.name, "status": kiosk.status},
            "printer": {
                "name": printer.name,
                "status": printer.status,
                "last_polled_at": printer.last_polled_at,
            },
            "commands": commands,
            "config": {
                "heartbeat_seconds": 30,
                "job_session_minutes": settings.JOB_SESSION_TIMEOUT_MINUTES,
            },
            "server_time": timezone.now(),
        }
    )
