"""Cada subdomínio serve só a sua parte da aplicação.

- HOST_CLIENT   → portal do cliente (+ API, para o portal/app usarem a mesma origem)
- HOST_ADMIN    → superadmin (/admin é exclusivo deste subdomínio)
- HOST_API      → só /api/ (webhooks de pagamento, Pi, apps)
- HOST_SCREEN   → ecrã do kiosk (/ecra/) + API
- HOST_APP      → nome antigo: reencaminha para HOST_SCREEN
- HOST_PARTNER  → portal do parceiro (/parceiro/ é exclusivo deste subdomínio)
- HOST_ROOTS    → domínio principal: reencaminha para o portal (/api/ continua a funcionar)

Sem HOST_CLIENT configurado (desenvolvimento) não há qualquer restrição.
"""

from django.conf import settings
from django.http import HttpResponseNotFound, HttpResponsePermanentRedirect, JsonResponse

ALWAYS = ("/static/", "/healthz")


def _url(host: str, path: str, query: str) -> str:
    return f"https://{host}{path}" + (f"?{query}" if query else "")


class HostRoutingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not settings.HOST_CLIENT:
            return self.get_response(request)
        host = request.get_host().split(":")[0].lower()
        path = request.path
        query = request.META.get("QUERY_STRING", "")
        if path.startswith(ALWAYS) or host in ("127.0.0.1", "localhost"):
            return self.get_response(request)

        is_admin_path = path.startswith("/admin/") or path == "/admin"
        is_api_path = path.startswith("/api/")
        is_partner_path = path.startswith("/parceiro/") or path == "/parceiro"

        if host == settings.HOST_PARTNER:
            if path == "/":
                return HttpResponsePermanentRedirect("/parceiro/")
            if is_partner_path or path.startswith("/i18n/"):
                return self.get_response(request)
            return HttpResponseNotFound()

        if host == settings.HOST_ADMIN:
            if path == "/":
                return HttpResponsePermanentRedirect("/admin/")
            if is_admin_path or path.startswith("/i18n/"):
                return self.get_response(request)
            return HttpResponseNotFound()

        if host == settings.HOST_API:
            if path == "/":
                return JsonResponse({"service": "StrongPrint API", "version": "v1"})
            if is_api_path:
                return self.get_response(request)
            return JsonResponse({"error": "not_found"}, status=404)

        if host == settings.HOST_CLIENT:
            if (is_admin_path and settings.HOST_ADMIN) or (
                is_partner_path and settings.HOST_PARTNER
            ):
                return HttpResponseNotFound()
            return self.get_response(request)

        if host == settings.HOST_SCREEN:
            if path == "/":
                return HttpResponsePermanentRedirect("/ecra/")
            if is_api_path or path.startswith("/ecra/"):
                return self.get_response(request)
            return HttpResponseNotFound()

        if host == settings.HOST_APP and settings.HOST_SCREEN:
            return HttpResponsePermanentRedirect(_url(settings.HOST_SCREEN, path, query))

        if host in settings.HOST_ROOTS:
            if is_api_path:
                return self.get_response(request)
            if is_admin_path:
                target = settings.HOST_ADMIN or settings.HOST_CLIENT
                return HttpResponsePermanentRedirect(_url(target, path, query))
            if is_partner_path and settings.HOST_PARTNER:
                return HttpResponsePermanentRedirect(_url(settings.HOST_PARTNER, path, query))
            return HttpResponsePermanentRedirect(_url(settings.HOST_CLIENT, path, query))

        return self.get_response(request)
