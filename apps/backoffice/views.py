from django.contrib import admin, messages
from django.shortcuts import render
from django.utils.translation import gettext as _

from . import metrics


def dashboard(request):
    """Página inicial do superadmin: visão completa do negócio."""
    if not request.user.is_superuser:
        # Equipa sem acesso a finanças: vê só a lista de modelos do admin.
        return admin.site.index(request)
    query = request.GET.get("q", "")
    context = {
        **admin.site.each_context(request),
        "title": _("Owner dashboard"),
        "sales": metrics.sales_summary(),
        "wallets": metrics.wallet_summary(),
        "partners": metrics.partner_rows(),
        "kiosks": metrics.kiosk_rows(),
        "daily": metrics.daily_series(30),
        "alerts": metrics.open_alerts(),
        "recent_jobs": metrics.recent_jobs(),
        "recent_topups": metrics.recent_topups(),
        "query": query,
        "customers": metrics.search_customers(query),
        "top_wallets": metrics.top_wallets(),
        "app_list": admin.site.get_app_list(request),
    }
    if query and not context["customers"]:
        messages.info(request, _("No customer found for “%(q)s”.") % {"q": query})
    return render(request, "admin/strongprint/dashboard.html", context)


def models_index(request):
    """Lista de modelos do admin (o índice original do Django)."""
    return admin.site.index(request)
