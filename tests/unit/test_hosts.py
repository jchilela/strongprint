import json

import pytest
from django.http import HttpResponse
from django.test import RequestFactory

from common.hosts import HostRoutingMiddleware

HOSTS = {
    "HOST_CLIENT": "client.sp.test",
    "HOST_ADMIN": "administ.sp.test",
    "HOST_API": "api.sp.test",
    "HOST_APP": "app.sp.test",
    "HOST_PARTNER": "partner.sp.test",
    "HOST_ROOTS": ["sp.test", "www.sp.test"],
}


@pytest.fixture
def route(settings):
    for key, value in HOSTS.items():
        setattr(settings, key, value)
    settings.ALLOWED_HOSTS = ["*"]
    middleware = HostRoutingMiddleware(lambda request: HttpResponse("app"))

    def call(host, path):
        return middleware(RequestFactory().get(path, HTTP_HOST=host))

    return call


@pytest.mark.parametrize(
    "host,path,status,location",
    [
        ("client.sp.test", "/carteira/", 200, None),
        ("client.sp.test", "/api/v1/wallet", 200, None),
        ("client.sp.test", "/admin/", 404, None),
        ("administ.sp.test", "/", 301, "/admin/"),
        ("administ.sp.test", "/admin/login/", 200, None),
        ("administ.sp.test", "/carteira/", 404, None),
        ("api.sp.test", "/api/v1/payments/webhook/strongpay", 200, None),
        ("api.sp.test", "/carteira/", 404, None),
        ("app.sp.test", "/x/", 301, "https://client.sp.test/x/"),
        ("app.sp.test", "/api/v1/wallet", 200, None),
        ("sp.test", "/entrar/?next=/a", 301, "https://client.sp.test/entrar/?next=/a"),
        ("www.sp.test", "/admin/", 301, "https://administ.sp.test/admin/"),
        ("sp.test", "/api/v1/edge/heartbeat", 200, None),
        ("sp.test", "/static/portal/portal.css", 200, None),
        ("127.0.0.1", "/healthz", 200, None),
        ("partner.sp.test", "/", 301, "/parceiro/"),
        ("partner.sp.test", "/parceiro/kiosks/", 200, None),
        ("partner.sp.test", "/carteira/", 404, None),
        ("client.sp.test", "/parceiro/", 404, None),
        ("sp.test", "/parceiro/", 301, "https://partner.sp.test/parceiro/"),
        ("other.test", "/", 200, None),
    ],
)
def test_host_routing(route, host, path, status, location):
    response = route(host, path)
    assert response.status_code == status
    if location:
        assert response["Location"] == location


def test_api_root(route):
    assert json.loads(route("api.sp.test", "/").content)["service"] == "StrongPrint API"


def test_without_admin_host_admin_lives_on_client(route, settings):
    settings.HOST_ADMIN = ""
    assert route("client.sp.test", "/admin/").status_code == 200
    assert route("sp.test", "/admin/")["Location"] == "https://client.sp.test/admin/"


def test_disabled_in_development(settings):
    settings.HOST_CLIENT = ""
    middleware = HostRoutingMiddleware(lambda request: HttpResponse("ok"))
    request = RequestFactory().get("/admin/", HTTP_HOST="testserver")
    assert middleware(request).status_code == 200
