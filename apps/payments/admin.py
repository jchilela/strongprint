from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from . import services
from .models import TopUp, WebhookEvent


class ReadOnlyAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TopUp)
class TopUpAdmin(ReadOnlyAdmin):
    list_display = (
        "created_at",
        "wallet",
        "amount_kz",
        "status",
        "entity",
        "reference",
        "provider",
        "paid_at",
    )
    list_filter = ("status", "provider")
    search_fields = ("reference", "provider_payment_id", "wallet__user__phone")
    list_select_related = ("wallet__user",)
    date_hierarchy = "created_at"
    actions = ["simulate_paid"]

    @admin.action(description=_("Simulate payment (mock provider only)"))
    def simulate_paid(self, request, queryset):
        for topup in queryset:
            try:
                services.simulate_payment(topup, "paid")
            except PermissionError as exc:
                messages.error(request, f"{topup}: {exc}")
            else:
                messages.success(request, f"{topup}: paid")


@admin.register(WebhookEvent)
class WebhookEventAdmin(ReadOnlyAdmin):
    list_display = ("received_at", "provider", "valid", "result", "topup")
    list_filter = ("provider", "valid", "result")
