from django import forms
from django.contrib import admin, messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from . import services
from .models import LedgerEntry, Wallet, WalletHold


class AdjustmentForm(forms.Form):
    amount_kz = forms.IntegerField(
        label=_("Amount (Kz)"), help_text=_("Positive to credit, negative to debit.")
    )
    memo = forms.CharField(label=_("Reason"), max_length=255)

    def clean_amount_kz(self):
        value = self.cleaned_data["amount_kz"]
        if value == 0:
            raise forms.ValidationError(_("Amount cannot be zero."))
        return value


class LedgerInline(admin.TabularInline):
    model = LedgerEntry
    extra = 0
    can_delete = False
    fields = (
        "created_at",
        "kind",
        "amount_kz",
        "balance_after_kz",
        "kiosk",
        "job",
        "actor",
        "memo",
    )
    readonly_fields = fields
    ordering = ["-created_at"]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("user", "balance_kz", "held_kz", "available", "updated_at", "adjust_link")
    search_fields = ("user__phone", "user__full_name")
    list_select_related = ("user",)
    readonly_fields = ("user", "balance_kz", "held_kz", "created_at", "updated_at")
    inlines = [LedgerInline]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description=_("available (Kz)"))
    def available(self, obj):
        return obj.available_kz

    @admin.display(description="")
    def adjust_link(self, obj):
        url = reverse("admin:wallets_wallet_adjust", args=[obj.pk])
        return format_html('<a href="{}">{}</a>', url, _("Adjust"))

    def get_urls(self):
        return [
            path(
                "<int:pk>/adjust/",
                self.admin_site.admin_view(self.adjust_view),
                name="wallets_wallet_adjust",
            ),
            *super().get_urls(),
        ]

    def adjust_view(self, request, pk):
        if not request.user.is_superuser:
            messages.error(request, _("Only superadmins can adjust wallets."))
            return redirect("admin:wallets_wallet_changelist")
        wallet = get_object_or_404(Wallet, pk=pk)
        form = AdjustmentForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                services.adjust(
                    wallet,
                    form.cleaned_data["amount_kz"],
                    actor=f"admin:{request.user.pk}",
                    memo=form.cleaned_data["memo"],
                )
            except services.InsufficientFunds as exc:
                form.add_error("amount_kz", str(exc))
            else:
                messages.success(request, _("Wallet adjusted."))
                return redirect("admin:wallets_wallet_change", pk)
        context = {
            **self.admin_site.each_context(request),
            "title": _("Adjust wallet: %s") % wallet.user,
            "wallet": wallet,
            "form": form,
            "opts": self.model._meta,
        }
        return render(request, "admin/wallets/adjust.html", context)


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = ("created_at", "wallet", "kind", "amount_kz", "kiosk", "job", "actor")
    list_filter = ("kind", "kiosk__site__partner", "kiosk__site", "kiosk")
    search_fields = ("wallet__user__phone", "reference", "job__id")
    list_select_related = ("wallet__user", "kiosk", "job")
    date_hierarchy = "created_at"

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WalletHold)
class WalletHoldAdmin(admin.ModelAdmin):
    list_display = (
        "job",
        "wallet",
        "amount_kz",
        "captured_kz",
        "status",
        "created_at",
        "settled_at",
    )
    list_filter = ("status",)
    list_select_related = ("job", "wallet__user")

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
