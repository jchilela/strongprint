from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from django.utils.translation import gettext_lazy as _

admin.site.site_header = _("StrongPrint — Superadmin")
admin.site.site_title = "StrongPrint"
admin.site.index_title = _("Administration")


def healthz(_request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthz),
    path("api/v1/", include("config.api_urls")),
    path("i18n/", include("django.conf.urls.i18n")),
    path("", include("apps.portal.urls")),
]
