from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from django.utils.translation import gettext_lazy as _

admin.site.site_header = _("StrongPrint — Superadmin")
admin.site.site_title = "StrongPrint"
admin.site.index_title = _("Administration")


def healthz(_request):
    return JsonResponse({"status": "ok"})


from apps.backoffice import views as backoffice  # noqa: E402

urlpatterns = [
    # Painel do dono como página inicial do superadmin; a lista de modelos fica em /admin/modelos/.
    path("admin/", admin.site.admin_view(backoffice.dashboard), name="strongprint-dashboard"),
    path("admin/modelos/", admin.site.admin_view(backoffice.models_index), name="admin-models"),
    path("admin/", admin.site.urls),
    path("healthz", healthz),
    path("api/v1/", include("config.api_urls")),
    path("i18n/", include("django.conf.urls.i18n")),
    path("parceiro/", include("apps.partner_portal.urls")),
    path("ecra/", include("apps.screen.urls")),
    path("", include("apps.portal.urls")),
]
