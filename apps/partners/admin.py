from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.devices.models import Printer

from .models import Kiosk, Partner, PartnerMembership, PriceTable, Site


class SiteInline(admin.TabularInline):
    model = Site
    extra = 0
    fields = ("number", "name", "city", "is_active")
    show_change_link = True


class MembershipInline(admin.TabularInline):
    model = PartnerMembership
    extra = 0
    autocomplete_fields = ("user",)


class KioskInline(admin.TabularInline):
    model = Kiosk
    extra = 0
    fields = ("number", "code", "name", "status", "is_active")
    readonly_fields = ("code", "status")
    show_change_link = True


class PriceTableInline(admin.StackedInline):
    model = PriceTable
    extra = 0
    max_num = 1
    fields = (
        ("bw_page_kz", "min_transaction_kz"),
        ("color_low_kz", "color_mid_kz", "color_high_kz"),
        ("color_low_max_pct", "color_mid_max_pct"),
    )
    verbose_name_plural = _("Price overrides (empty fields inherit)")


class PartnerPriceInline(PriceTableInline):
    fk_name = "partner"


class SitePriceInline(PriceTableInline):
    fk_name = "site"


class KioskPriceInline(PriceTableInline):
    fk_name = "kiosk"


class PrinterInline(admin.StackedInline):
    model = Printer
    extra = 0
    max_num = 1
    fields = (
        ("name", "vpn_ip"),
        ("status", "last_page_counter", "last_polled_at"),
        "panel_enabled",
    )
    readonly_fields = ("name", "status", "last_page_counter", "last_polled_at")
    show_change_link = True


@admin.register(Partner)
class PartnerAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "nif",
        "contact_name",
        "contact_phone",
        "site_count",
        "is_active",
    )
    list_filter = ("is_active",)
    search_fields = ("code", "name", "nif", "contact_name")
    inlines = [SiteInline, MembershipInline, PartnerPriceInline]

    @admin.display(description=_("sites"))
    def site_count(self, obj):
        return obj.sites.count()


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ("site_code", "name", "partner", "city", "map_link", "is_active")
    list_filter = ("partner", "is_active", "city")
    search_fields = ("name", "partner__name", "partner__code", "city")
    fields = (
        "partner",
        "number",
        "site_code",
        "name",
        "city",
        "address",
        "latitude",
        "longitude",
        "is_active",
    )
    readonly_fields = ("site_code",)
    list_select_related = ("partner",)
    inlines = [KioskInline, SitePriceInline]

    @admin.display(description=_("code"))
    def site_code(self, obj):
        return obj.code if obj.pk else "—"

    @admin.display(description=_("map"))
    def map_link(self, obj):
        if obj.latitude is None or obj.longitude is None:
            return "—"
        url = f"https://www.google.com/maps?q={obj.latitude},{obj.longitude}"
        return format_html('<a href="{}" target="_blank" rel="noopener">{}</a>', url, _("Open"))


@admin.register(Kiosk)
class KioskAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "site",
        "status",
        "printer_status",
        "last_heartbeat",
        "open_alerts",
        "is_active",
    )
    list_filter = ("status", "is_active", "site__partner", "site")
    search_fields = ("code", "name", "site__name", "site__partner__name")
    list_select_related = ("site__partner", "printer__edge_device")
    inlines = [PrinterInline, KioskPriceInline]

    @admin.display(description=_("printer"))
    def printer_status(self, obj):
        printer = getattr(obj, "printer", None)
        return printer.get_status_display() if printer else "—"

    @admin.display(description=_("open alerts"))
    def open_alerts(self, obj):
        return obj.alerts.filter(resolved_at__isnull=True).count() or "—"

    @admin.display(description=_("last heartbeat"))
    def last_heartbeat(self, obj):
        printer = getattr(obj, "printer", None)
        device = getattr(printer, "edge_device", None) if printer else None
        return device.last_heartbeat if device else None


@admin.register(PartnerMembership)
class PartnerMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "partner", "role")
    list_filter = ("role", "partner")
    search_fields = ("user__phone", "user__full_name", "partner__name")
    autocomplete_fields = ("user", "partner")


@admin.register(PriceTable)
class PriceTableAdmin(admin.ModelAdmin):
    list_display = (
        "scope",
        "bw_page_kz",
        "color_low_kz",
        "color_mid_kz",
        "color_high_kz",
        "min_transaction_kz",
    )
    list_select_related = ("partner", "site", "kiosk")
    autocomplete_fields = ("partner", "site", "kiosk")

    @admin.display(description=_("scope"))
    def scope(self, obj):
        return obj.scope_label
