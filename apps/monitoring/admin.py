from django.contrib import admin
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Alert


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
