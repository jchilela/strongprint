from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import InboundEmail, Job, JobEvent


class JobEventInline(admin.TabularInline):
    model = JobEvent
    extra = 0
    can_delete = False
    fields = ("created_at", "from_state", "to_state", "actor", "data")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    """Só leitura: o estado de um Job muda apenas através do orquestrador."""

    list_display = (
        "short_id",
        "kiosk",
        "customer",
        "state",
        "source",
        "pages",
        "total_kz",
        "pages_printed",
        "created_at",
    )
    list_filter = ("state", "source", "kiosk__site__partner", "kiosk")
    search_fields = ("id", "customer__phone", "original_filename", "kiosk__code")
    list_select_related = ("kiosk", "customer")
    date_hierarchy = "created_at"
    inlines = [JobEventInline]

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description=_("ID"))
    def short_id(self, obj):
        return str(obj.id)[:8]


@admin.register(InboundEmail)
class InboundEmailAdmin(admin.ModelAdmin):
    list_display = ("received_at", "sender", "subject", "phone", "status", "files", "customer")
    list_filter = ("status",)
    search_fields = ("sender", "subject", "phone")
    list_select_related = ("customer",)
    date_hierarchy = "received_at"

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False
