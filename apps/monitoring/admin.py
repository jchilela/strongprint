from django.contrib import admin
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Alert, Downtime, format_duration


class OpenFilter(admin.SimpleListFilter):
    title = _("status")
    parameter_name = "open"

    def lookups(self, request, model_admin):
        return [("1", _("Open")), ("0", _("Resolved"))]

    def queryset(self, request, queryset):
        if self.value() == "1":
            return queryset.filter(resolved_at__isnull=True)
        if self.value() == "0":
            return queryset.filter(resolved_at__isnull=False)
        return queryset


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = (
        "opened_at",
        "severity",
        "kind",
        "kiosk",
        "message",
        "last_seen_at",
        "resolved_at",
    )
    list_filter = (OpenFilter, "severity", "kind", "kiosk__site__partner", "kiosk")
    search_fields = ("message", "kiosk__code")
    list_select_related = ("kiosk",)
    actions = ["resolve"]

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    @admin.action(description=_("Mark as resolved"))
    def resolve(self, request, queryset):
        queryset.filter(resolved_at__isnull=True).update(resolved_at=timezone.now())


class DowntimeOpenFilter(admin.SimpleListFilter):
    title = _("status")
    parameter_name = "open"

    def lookups(self, request, model_admin):
        return [("1", _("Still unavailable")), ("0", _("Ended"))]

    def queryset(self, request, queryset):
        if self.value() == "1":
            return queryset.filter(ended_at__isnull=True)
        if self.value() == "0":
            return queryset.filter(ended_at__isnull=False)
        return queryset


@admin.register(Downtime)
class DowntimeAdmin(admin.ModelAdmin):
    list_display = (
        "started_at",
        "kiosk",
        "reason",
        "printer_reasons",
        "ended_at",
        "duration_display",
        "emailed",
    )
    list_filter = (DowntimeOpenFilter, "reason", "kiosk__site__partner", "kiosk")
    search_fields = ("kiosk__code",)
    list_select_related = ("kiosk",)
    date_hierarchy = "started_at"

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    @admin.display(description=_("printer said"))
    def printer_reasons(self, obj):
        return ", ".join((obj.detail or {}).get("reasons", [])) or "—"

    @admin.display(description=_("duration"))
    def duration_display(self, obj):
        text = format_duration(obj.duration)
        return f"{text} ({_('ongoing')})" if obj.is_open else text

    @admin.display(description=_("e-mail"), boolean=True)
    def emailed(self, obj):
        return obj.notified_at is not None
