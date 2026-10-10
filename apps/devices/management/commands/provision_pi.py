"""Regista um Raspberry Pi na VPN e devolve a sua configuração em JSON.

Usado por deploy/kiosk/provision.sh (no servidor, via docker compose exec):

    python manage.py provision_pi IMETRO-S01-K01-PI --public-key <wg pub> \\
        --lan-ip 192.168.1.238 --ssh-user julio --printer-lan-ip 192.168.1.50 --issue-token

- atribui (ou mantém) o slot N → Pi 10.77.N.1, impressora 10.77.N.2;
- guarda IP local, utilizador SSH e chave pública WireGuard no admin;
- aponta a impressora para o IP da VPN (o Pi encaminha-o para o IP local);
- com --issue-token gera um token novo do Pi (só aparece neste JSON).
"""

import json
import re

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.devices.models import EdgeDevice

WG_KEY = re.compile(r"^[A-Za-z0-9+/]{42}[AEIMQUYcgkosw480]=$")


def next_slot() -> int:
    used = set(EdgeDevice.objects.exclude(vpn_slot=None).values_list("vpn_slot", flat=True))
    for slot in range(1, 255):
        if slot not in used:
            return slot
    raise CommandError("No free VPN slots (1-254)")


class Command(BaseCommand):
    help = "Regista um Pi na VPN WireGuard e devolve a configuração (JSON)."

    def add_arguments(self, parser):
        parser.add_argument("device", help="Nome do Pi, ex.: IMETRO-S01-K01-PI")
        parser.add_argument("--public-key", required=True)
        parser.add_argument("--lan-ip")
        parser.add_argument("--ssh-user")
        parser.add_argument("--printer-lan-ip")
        parser.add_argument("--issue-token", action="store_true")

    def handle(self, *args, **opts):
        if not WG_KEY.match(opts["public_key"]):
            raise CommandError("Invalid WireGuard public key")
        with transaction.atomic():
            device = (
                EdgeDevice.objects.select_for_update()
                .select_related("printer")
                .filter(name=opts["device"])
                .first()
            )
            if device is None:
                raise CommandError(f"Unknown device {opts['device']}")
            if device.vpn_slot is None:
                device.vpn_slot = next_slot()
            prefix = f"{settings.VPN_PREFIX}.{device.vpn_slot}"
            device.vpn_ip = f"{prefix}.1"
            device.wg_public_key = opts["public_key"]
            device.lan_ip = opts.get("lan_ip") or device.lan_ip
            device.ssh_user = opts.get("ssh_user") or device.ssh_user
            device.provisioned_at = timezone.now()
            token = device.issue_token() if opts["issue_token"] else None
            device.save()

            printer = device.printer
            printer.vpn_ip = f"{prefix}.2"
            printer.lan_ip = opts.get("printer_lan_ip") or printer.lan_ip
            printer.ipp_uri = ""  # usa ipp://<VPN IP>/ipp/print
            printer.save()

        result = {
            "device": device.name,
            "kiosk": device.kiosk.code if device.kiosk else None,
            "slot": device.vpn_slot,
            "pi_vpn_ip": device.vpn_ip,
            "printer_vpn_ip": printer.vpn_ip,
            "printer_lan_ip": printer.lan_ip,
            "subnet": f"{prefix}.0/24",
            "server_vpn_ip": settings.VPN_SERVER_IP,
            "server_public_key": settings.VPN_SERVER_PUBLIC_KEY,
            "endpoint": settings.VPN_ENDPOINT,
            "api_url": f"https://{settings.HOST_API}" if settings.HOST_API else "",
            "screen_url": f"https://{settings.HOST_SCREEN}/ecra/" if settings.HOST_SCREEN else "",
        }
        if token:
            result["token"] = token
        self.stdout.write(json.dumps(result))
