import hashlib
import secrets
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.partners.models import Kiosk
from common.models import TimeStampedModel


def hash_device_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_pairing_code() -> str:
    # Sem caracteres ambíguos (0/O, 1/l): pode ter de ser escrito à mão.
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))


class EdgeDevice(TimeStampedModel):
    """Raspberry Pi de um kiosk (ecrã + gateway VPN + leitor de pens USB)."""

    class Command(models.TextChoices):
        NONE = "", _("None")
        RELOAD = "reload", _("Reload screen")
        REBOOT = "reboot", _("Reboot")
        OUT_OF_SERVICE = "out_of_service", _("Out of service")

    # O Pi pertence a uma impressora; o kiosk vem da impressora.
    printer = models.OneToOneField(
        "Printer", on_delete=models.PROTECT, related_name="edge_device", verbose_name=_("printer")
    )
    name = models.CharField(
        _("name"), max_length=100, blank=True, help_text=_("Generated from the printer's kiosk.")
    )
    device_token_hash = models.CharField(max_length=64, unique=True, editable=False)
    token_prefix = models.CharField(_("token prefix"), max_length=8, editable=False)
    vpn_ip = models.GenericIPAddressField(_("VPN IP"), null=True, blank=True)
    last_heartbeat = models.DateTimeField(_("last heartbeat"), null=True, blank=True)
    version = models.CharField(_("software version"), max_length=40, blank=True)
    pending_command = models.CharField(
        _("pending command"), max_length=20, choices=Command.choices, blank=True, default=""
    )
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("edge device (Pi)")
        verbose_name_plural = _("edge devices (Pi)")
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def kiosk(self) -> Kiosk | None:
        return self.printer.kiosk

    def save(self, *args, **kwargs):
        self.name = f"{self.printer.base_name}-PI"
        super().save(*args, **kwargs)

    def issue_token(self) -> str:
        """Gera um token novo e devolve-o em claro. Só o hash fica guardado."""
        token = secrets.token_urlsafe(32)
        self.device_token_hash = hash_device_token(token)
        self.token_prefix = token[:8]
        return token

    @classmethod
    def authenticate(cls, token: str) -> "EdgeDevice | None":
        if not token:
            return None
        return (
            cls.objects.select_related("printer__kiosk")
            .filter(device_token_hash=hash_device_token(token), is_active=True)
            .first()
        )


class Printer(TimeStampedModel):
    class Status(models.TextChoices):
        UNKNOWN = "UNKNOWN", _("Unknown")
        READY = "READY", _("Ready")
        BUSY = "BUSY", _("Busy")
        WARNING = "WARNING", _("Warning")
        ERROR = "ERROR", _("Error")
        OFFLINE = "OFFLINE", _("Offline")

    class Gateway(models.TextChoices):
        DEFAULT = "", _("Default (from settings)")
        FAKE = "fake", _("Fake (tests)")
        IPP = "ipp", _("IPP (real printer)")

    kiosk = models.OneToOneField(
        Kiosk, null=True, blank=True, on_delete=models.SET_NULL, related_name="printer"
    )
    name = models.CharField(
        _("name"),
        max_length=100,
        blank=True,
        help_text=_("Generated when the printer is in a kiosk; otherwise a name for the spare."),
    )
    model = models.CharField(
        _("model"), max_length=100, default="HP Color LaserJet Pro MFP 3303sdw"
    )
    serial_number = models.CharField(_("serial number"), max_length=60, blank=True)
    vpn_ip = models.GenericIPAddressField(_("VPN IP"))
    ipp_uri = models.CharField(
        _("IPP URI"), max_length=255, blank=True, help_text=_("Empty = ipp://<VPN IP>/ipp/print")
    )
    snmp_community = models.CharField(_("SNMP community"), max_length=60, default="public")
    gateway = models.CharField(
        _("gateway"), max_length=10, choices=Gateway.choices, blank=True, default=""
    )

    supports_color = models.BooleanField(_("colour"), default=True)
    supports_duplex = models.BooleanField(_("duplex"), default=True)
    supports_a3 = models.BooleanField(_("A3"), default=False)
    has_adf = models.BooleanField(_("ADF"), default=True)
    panel_enabled = models.BooleanField(
        _("front panel enabled"),
        default=False,
        help_text=_(
            "Walk-up use of the printer panel (copy/USB). Off by default because it "
            "affects the page counter used to verify jobs."
        ),
    )

    status = models.CharField(
        _("status"), max_length=10, choices=Status.choices, default=Status.UNKNOWN
    )
    status_detail = models.JSONField(_("status detail"), default=dict, blank=True)
    last_page_counter = models.PositiveBigIntegerField(
        _("last page counter"), null=True, blank=True
    )
    last_polled_at = models.DateTimeField(_("last polled at"), null=True, blank=True)

    class Meta:
        verbose_name = _("printer")
        verbose_name_plural = _("printers")
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.vpn_ip})"

    @property
    def base_name(self) -> str:
        """Kiosk onde está a impressora (ou o nome da impressora suplente, sem -PRN)."""
        if self.kiosk_id:
            return self.kiosk.code
        return self.name.removesuffix("-PRN") or f"SPARE-{self.pk}"

    def clean(self):
        super().clean()
        if not self.kiosk_id and not self.name:
            raise ValidationError(_("A printer outside a kiosk needs a name."))

    def save(self, *args, **kwargs):
        if self.kiosk_id:
            self.name = f"{self.kiosk.code}-PRN"
        super().save(*args, **kwargs)
        device = getattr(self, "edge_device", None)
        if device is not None:
            device.save()

    @property
    def effective_ipp_uri(self) -> str:
        return self.ipp_uri or f"ipp://{self.vpn_ip}/ipp/print"

    @property
    def capabilities(self) -> dict:
        return {
            "color": self.supports_color,
            "duplex": self.supports_duplex,
            "a3": self.supports_a3,
            "adf": self.has_adf,
        }


class KioskPairing(models.Model):
    """Ligação do ecrã do kiosk à conta de um cliente através de um QR.

    O ecrã cria o emparelhamento e mostra o QR; o cliente lê-o no telemóvel
    (com sessão iniciada no portal) e confirma; o ecrã recebe então, uma única
    vez, o token de sessão do kiosk.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", _("Waiting for the phone")
        CLAIMED = "CLAIMED", _("Confirmed on the phone")
        CONSUMED = "CONSUMED", _("Used by the kiosk")
        EXPIRED = "EXPIRED", _("Expired")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kiosk = models.ForeignKey(Kiosk, on_delete=models.CASCADE, related_name="pairings")
    code = models.CharField(max_length=16, unique=True, default=new_pairing_code)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE
    )
    expires_at = models.DateTimeField()
    claimed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("kiosk pairing")
        verbose_name_plural = _("kiosk pairings")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.kiosk} · {self.status}"

    @property
    def is_open(self) -> bool:
        return self.status == self.Status.PENDING and self.expires_at > timezone.now()
