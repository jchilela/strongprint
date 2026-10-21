from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models import Max, Q
from django.utils.translation import gettext_lazy as _

from common.models import TimeStampedModel


def _next_number(queryset) -> int:
    return (queryset.aggregate(m=Max("number"))["m"] or 0) + 1


class Partner(TimeStampedModel):
    """Instituição parceira (ex.: universidade) que acolhe kiosks.

    Nomenclatura dos equipamentos: ``<PARCEIRO>-S<nn>-K<nn>`` para o kiosk e os
    sufixos ``-PRN`` (impressora) e ``-PI`` (Raspberry Pi). Ex.: IMETRO-S01-K01-PI.
    """

    name = models.CharField(_("name"), max_length=150)
    code = models.CharField(
        _("code"),
        max_length=12,
        unique=True,
        validators=[RegexValidator(r"^[A-Z0-9]+$", _("Use uppercase letters and digits only."))],
        help_text=_("Short code used in equipment names, e.g. IMETRO."),
    )
    nif = models.CharField(_("tax ID (NIF)"), max_length=30, blank=True)
    contact_name = models.CharField(_("contact name"), max_length=150, blank=True)
    contact_phone = models.CharField(_("contact phone"), max_length=20, blank=True)
    contact_email = models.EmailField(_("contact email"), blank=True)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("partner")
        verbose_name_plural = _("partners")
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.code = (self.code or "").upper()
        super().save(*args, **kwargs)
        for kiosk in Kiosk.objects.filter(site__partner=self):
            kiosk.save()  # recalcula códigos e nomes dos equipamentos


class PartnerMembership(TimeStampedModel):
    """Acesso de um utilizador ao portal do parceiro."""

    class Role(models.TextChoices):
        OWNER = "OWNER", _("Owner")
        VIEWER = "VIEWER", _("Viewer")

    partner = models.ForeignKey(Partner, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="partner_memberships"
    )
    role = models.CharField(_("role"), max_length=10, choices=Role.choices, default=Role.VIEWER)

    class Meta:
        verbose_name = _("partner membership")
        verbose_name_plural = _("partner memberships")
        constraints = [
            models.UniqueConstraint(fields=["partner", "user"], name="uniq_partner_member"),
        ]

    def __str__(self):
        return f"{self.user} @ {self.partner} ({self.role})"


class Site(TimeStampedModel):
    """Local físico de um parceiro (campus, faculdade, biblioteca)."""

    partner = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name="sites")
    number = models.PositiveSmallIntegerField(
        _("number"), blank=True, help_text=_("Empty = next available (S01, S02, ...).")
    )
    name = models.CharField(_("name"), max_length=150, blank=True, help_text=_("Empty = Site-NN."))
    city = models.CharField(_("city"), max_length=100, blank=True)
    address = models.CharField(_("address"), max_length=255, blank=True)
    latitude = models.DecimalField(
        _("latitude"),
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
    )
    longitude = models.DecimalField(
        _("longitude"),
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
    )
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("site")
        verbose_name_plural = _("sites")
        ordering = ["partner__code", "number"]
        constraints = [
            models.UniqueConstraint(fields=["partner", "number"], name="uniq_site_number"),
        ]

    def __str__(self):
        return f"{self.code} · {self.name}"

    @property
    def code(self) -> str:
        return f"{self.partner.code}-S{self.number:02d}"

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = _next_number(Site.objects.filter(partner=self.partner))
        if not self.name:
            self.name = f"Site-{self.number:02d}"
        super().save(*args, **kwargs)
        for kiosk in self.kiosks.all():
            kiosk.save()


class Kiosk(TimeStampedModel):
    class Status(models.TextChoices):
        ONLINE = "ONLINE", _("Online")
        OFFLINE = "OFFLINE", _("Offline")
        OUT_OF_SERVICE = "OUT_OF_SERVICE", _("Out of service")
        MAINTENANCE = "MAINTENANCE", _("Maintenance")

    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="kiosks")
    number = models.PositiveSmallIntegerField(
        _("number"), blank=True, help_text=_("Empty = next available in the site (K01, K02, ...).")
    )
    code = models.CharField(
        _("code"),
        max_length=40,
        unique=True,
        editable=False,
        help_text=_("Generated: <PARTNER>-S<nn>-K<nn>, e.g. IMETRO-S01-K01."),
    )
    name = models.CharField(_("name"), max_length=150, blank=True, help_text=_("Empty = Kiosk-NN."))
    location_hint = models.CharField(_("location hint"), max_length=255, blank=True)
    status = models.CharField(
        _("status"), max_length=20, choices=Status.choices, default=Status.OFFLINE
    )
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("kiosk")
        verbose_name_plural = _("kiosks")
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(fields=["site", "number"], name="uniq_kiosk_number"),
        ]

    def __str__(self):
        return self.code

    @property
    def partner(self):
        return self.site.partner

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = _next_number(Kiosk.objects.filter(site=self.site))
        if not self.name:
            self.name = f"Kiosk-{self.number:02d}"
        self.code = f"{self.site.code}-K{self.number:02d}"
        super().save(*args, **kwargs)
        printer = getattr(self, "printer", None)
        if printer is not None:
            printer.save()  # o nome da impressora (e do Pi) segue o código do kiosk


class PriceTable(TimeStampedModel):
    """Tabela de preços herdável: Kiosk → Site → Partner → global.

    Cada campo vazio herda do nível acima. A tabela global (sem âmbito) deve
    ter todos os campos preenchidos. Valores em Kz inteiros.
    """

    partner = models.ForeignKey(
        Partner, null=True, blank=True, on_delete=models.CASCADE, related_name="price_tables"
    )
    site = models.ForeignKey(
        Site, null=True, blank=True, on_delete=models.CASCADE, related_name="price_tables"
    )
    kiosk = models.ForeignKey(
        Kiosk, null=True, blank=True, on_delete=models.CASCADE, related_name="price_tables"
    )

    bw_page_kz = models.PositiveIntegerField(_("B&W page (Kz)"), null=True, blank=True)
    color_low_kz = models.PositiveIntegerField(
        _("colour, low coverage (Kz)"), null=True, blank=True
    )
    color_mid_kz = models.PositiveIntegerField(
        _("colour, mid coverage (Kz)"), null=True, blank=True
    )
    color_high_kz = models.PositiveIntegerField(
        _("colour, high coverage (Kz)"), null=True, blank=True
    )
    color_low_max_pct = models.PositiveSmallIntegerField(
        _("low coverage up to (%)"), null=True, blank=True
    )
    color_mid_max_pct = models.PositiveSmallIntegerField(
        _("mid coverage up to (%)"), null=True, blank=True
    )
    min_transaction_kz = models.PositiveIntegerField(
        _("minimum per transaction (Kz)"), null=True, blank=True
    )
    # Cópias têm preço próprio a preto (mercado: ~30 Kz); a cores usam os escalões acima.
    copy_bw_page_kz = models.PositiveIntegerField(_("copy, B&W page (Kz)"), null=True, blank=True)
    copy_min_transaction_kz = models.PositiveIntegerField(
        _("copy, minimum per transaction (Kz)"), null=True, blank=True
    )

    PRICE_FIELDS = (
        "bw_page_kz",
        "color_low_kz",
        "color_mid_kz",
        "color_high_kz",
        "color_low_max_pct",
        "color_mid_max_pct",
        "min_transaction_kz",
        "copy_bw_page_kz",
        "copy_min_transaction_kz",
    )

    class Meta:
        verbose_name = _("price table")
        verbose_name_plural = _("price tables")
        constraints = [
            models.CheckConstraint(
                name="pricetable_single_scope",
                condition=(
                    Q(partner__isnull=True, site__isnull=True)
                    | Q(partner__isnull=True, kiosk__isnull=True)
                    | Q(site__isnull=True, kiosk__isnull=True)
                ),
            ),
            models.UniqueConstraint(
                fields=["partner"], condition=Q(partner__isnull=False), name="uniq_price_partner"
            ),
            models.UniqueConstraint(
                fields=["site"], condition=Q(site__isnull=False), name="uniq_price_site"
            ),
            models.UniqueConstraint(
                fields=["kiosk"], condition=Q(kiosk__isnull=False), name="uniq_price_kiosk"
            ),
            models.UniqueConstraint(
                fields=["partner", "site", "kiosk"],
                condition=Q(partner__isnull=True, site__isnull=True, kiosk__isnull=True),
                nulls_distinct=False,
                name="uniq_price_global",
            ),
        ]

    def __str__(self):
        return f"{_('Price table')}: {self.scope_label}"

    @property
    def scope_label(self) -> str:
        if self.kiosk_id:
            return f"kiosk {self.kiosk}"
        if self.site_id:
            return f"site {self.site}"
        if self.partner_id:
            return f"partner {self.partner}"
        return "global"

    def clean(self):
        super().clean()
        scopes = [s for s in (self.partner_id, self.site_id, self.kiosk_id) if s]
        if len(scopes) > 1:
            raise ValidationError(_("A price table applies to only one partner, site or kiosk."))
        if not scopes:
            missing = [f for f in self.PRICE_FIELDS if getattr(self, f) is None]
            if missing:
                raise ValidationError(_("The global price table must define every field."))
        low, mid = self.color_low_max_pct, self.color_mid_max_pct
        if low is not None and mid is not None and not (0 < low < mid <= 100):
            raise ValidationError(_("Coverage thresholds must satisfy 0 < low < mid ≤ 100."))
