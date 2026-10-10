from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from .models import EdgeDevice, Printer


def _show_token(request, device, token):
    messages.warning(
        request,
        _("Device token for %(name)s (shown only once, copy it to the Pi now): %(token)s")
        % {"name": device.name, "token": token},
    )


@admin.register(EdgeDevice)
class EdgeDeviceAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "printer",
        "kiosk",
        "vpn_ip",
        "version",
        "last_heartbeat",
        "pending_command",
        "is_active",
    )
    list_filter = ("is_active", "pending_command", "printer__kiosk__site__partner")
    search_fields = ("name", "printer__name", "printer__kiosk__code", "vpn_ip", "token_prefix")
    list_select_related = ("printer__kiosk",)
    readonly_fields = (
        "name",
        "kiosk",
        "token_prefix",
        "last_heartbeat",
        "version",
        "created_at",
        "updated_at",
    )
    fields = (
        "printer",
        "name",
        "kiosk",
        "vpn_ip",
        "pending_command",
        "is_active",
        "token_prefix",
        "version",
        "last_heartbeat",
        "created_at",
        "updated_at",
    )
    actions = ["regenerate_token"]

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "printer":
            # Só impressoras ainda sem Pi (mais a atual, ao editar).
            current = request.resolver_match.kwargs.get("object_id")
            free = Printer.objects.filter(edge_device__isnull=True)
            if current:
                free = free | Printer.objects.filter(edge_device__pk=current)
            kwargs["queryset"] = free.select_related("kiosk").distinct()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @admin.display(description=_("kiosk"))
    def kiosk(self, obj):
        return obj.printer.kiosk if obj.pk else "—"

    def save_model(self, request, obj, form, change):
        token = None if change else obj.issue_token()
        super().save_model(request, obj, form, change)
        if token:
            _show_token(request, obj, token)

    @admin.action(description=_("Regenerate device token"))
    def regenerate_token(self, request, queryset):
        for device in queryset:
            token = device.issue_token()
            device.save(update_fields=["device_token_hash", "token_prefix", "updated_at"])
            _show_token(request, device, token)


class EdgeDeviceOfPrinterInline(admin.StackedInline):
    model = EdgeDevice
    extra = 0
    max_num = 1
    can_delete = False
    fields = ("name", "vpn_ip", "version", "last_heartbeat", "token_prefix", "is_active")
    readonly_fields = fields
    show_change_link = True
    verbose_name_plural = _("Pi (create it under Edge devices to get the token)")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Printer)
class PrinterAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "kiosk",
        "vpn_ip",
        "status",
        "last_page_counter",
        "last_polled_at",
        "panel_enabled",
        "gateway",
    )
    list_filter = ("status", "gateway", "panel_enabled", "kiosk__site__partner")
    search_fields = ("name", "kiosk__code", "vpn_ip", "serial_number")
    list_select_related = ("kiosk",)
    autocomplete_fields = ("kiosk",)
    readonly_fields = ("status", "status_detail", "last_page_counter", "last_polled_at")
    inlines = [EdgeDeviceOfPrinterInline]
    fieldsets = (
        (None, {"fields": ("kiosk", "name", "model", "serial_number")}),
        (_("Network"), {"fields": ("vpn_ip", "ipp_uri", "snmp_community", "gateway")}),
        (
            _("Capabilities"),
            {
                "fields": (
                    "supports_color",
                    "supports_duplex",
                    "supports_a3",
                    "has_adf",
                    "panel_enabled",
                )
            },
        ),
        (
            _("Live status"),
            {"fields": ("status", "status_detail", "last_page_counter", "last_polled_at")},
        ),
    )
