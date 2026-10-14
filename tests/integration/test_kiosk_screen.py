import shutil
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.devices.models import KioskPairing, Printer
from apps.jobs import library
from apps.jobs.models import Document
from apps.partners.models import Kiosk
from domain.jobs.state_machine import JobState
from tests.docs import make_pdf, page
from tests.factories import EdgeDeviceFactory, UserFactory

pytestmark = pytest.mark.django_db
needs_tools = pytest.mark.skipif(not shutil.which("gs"), reason="needs ghostscript")


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def device(kiosk):
    kiosk.printer.status = Printer.Status.READY
    kiosk.printer.save()
    return EdgeDeviceFactory(printer=kiosk.printer)


@pytest.fixture
def screen(device):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Device {device.plain_token}")
    return client


def session_client(token):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Kiosk {token}")
    return client


def test_screen_page_renders(client):
    response = client.get("/ecra/")
    assert response.status_code == 200
    assert b"screen.js" in response.content and b"SP_CONFIG" in response.content
    assert response["X-Frame-Options"] == "DENY"


def test_kiosk_state(screen, kiosk):
    from django.core.cache import cache

    data = screen.get("/api/v1/kiosk/state?screen=853x533@1.5").json()
    assert cache.get(f"kiosk_screen:{kiosk.pk}") | {"at": None} == {"w": 853, "h": 533, "dpr": 1.5, "at": None}
    screen.get("/api/v1/kiosk/state?screen=evil")  # valores estranhos são ignorados
    assert cache.get(f"kiosk_screen:{kiosk.pk}")["w"] == 853
    assert data["kiosk"]["code"] == kiosk.code
    assert data["available"] is True and data["prices"]["bw_page_kz"] == 100

    Kiosk.objects.filter(pk=kiosk.pk).update(status=Kiosk.Status.MAINTENANCE)
    data = screen.get("/api/v1/kiosk/state").json()
    assert (data["available"], data["reason"]) == (False, "maintenance")

    Kiosk.objects.filter(pk=kiosk.pk).update(status=Kiosk.Status.ONLINE)
    Printer.objects.filter(pk=kiosk.printer.pk).update(
        status=Printer.Status.ERROR, status_detail={"paper_out": True}
    )
    data = screen.get("/api/v1/kiosk/state").json()
    assert (data["available"], data["reason"]) == (False, "paper_out")

    Printer.objects.filter(pk=kiosk.printer.pk).update(
        status=Printer.Status.ERROR, status_detail={"reasons": ["media-jam-error"]}
    )
    data = screen.get("/api/v1/kiosk/state").json()
    assert (data["available"], data["reason"]) == (False, "paper_jam")

    Printer.objects.filter(pk=kiosk.printer.pk).update(
        status=Printer.Status.READY, status_detail={"reasons": ["toner-low-warning"]}
    )
    data = screen.get("/api/v1/kiosk/state").json()
    assert (data["available"], data["reason"]) == (True, "")


def test_kiosk_api_requires_device_token():
    assert APIClient().get("/api/v1/kiosk/state").status_code in (401, 403)
    bad = APIClient()
    bad.credentials(HTTP_AUTHORIZATION="Device nope")
    assert bad.post("/api/v1/kiosk/pairings").status_code == 401


def test_qr_pairing_flow(screen, kiosk, client):
    pairing = screen.post("/api/v1/kiosk/pairings").json()
    assert pairing["qr_svg"].startswith("<svg") and f"/k/{pairing['code']}/" in pairing["url"]
    poll = f"/api/v1/kiosk/pairings/{pairing['id']}"
    assert screen.get(poll).json() == {"status": "PENDING"}

    # telemóvel: o QR leva ao endereço em maiúsculas (QR mais simples); sem sessão → entrar
    assert reverse("portal:kiosk_pair", args=[pairing["code"]]) == f"/k/{pairing['code']}/"
    url = f"/K/{pairing['code']}/"
    assert "/entrar/" in client.get(url).url

    user = UserFactory(full_name="Ana")
    client.force_login(user)
    page_ = client.get(url)  # ler o QR já liga: sem botão a confirmar
    assert page_.status_code == 200 and kiosk.code in page_.content.decode()
    assert page_.context["done"] is True

    data = screen.get(poll).json()
    assert data["status"] == "CLAIMED" and data["user"]["name"] == "Ana"
    assert screen.get(poll).json() == {"status": "CONSUMED"}  # o token só sai uma vez

    me = session_client(data["token"]).get("/api/v1/me").json()
    assert me["name"] == "Ana" and me["needs_name"] is False

    # recarregar a página continua a dizer "ligado"; para outra pessoa o QR já não serve
    assert client.get(url).context["done"] is True
    client.force_login(UserFactory(full_name="Rui"))
    assert client.get(url).status_code == 410


def test_new_pairing_expires_previous_and_ttl(screen, kiosk):
    first = screen.post("/api/v1/kiosk/pairings").json()
    second = screen.post("/api/v1/kiosk/pairings").json()
    assert KioskPairing.objects.get(pk=first["id"]).status == KioskPairing.Status.EXPIRED
    KioskPairing.objects.filter(pk=second["id"]).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert screen.get(f"/api/v1/kiosk/pairings/{second['id']}").json()["status"] == "EXPIRED"
    other = EdgeDeviceFactory()
    stranger = APIClient()
    stranger.credentials(HTTP_AUTHORIZATION=f"Device {other.plain_token}")
    assert stranger.get(f"/api/v1/kiosk/pairings/{second['id']}").status_code == 404


def test_pairing_needs_name_first(screen, client):
    pairing = screen.post("/api/v1/kiosk/pairings").json()
    client.force_login(UserFactory(full_name=""))
    url = f"/K/{pairing['code']}/"
    page_ = client.get(url)  # pede o nome primeiro; ainda não liga
    assert page_.status_code == 200 and page_.context["waiting_name"] is True
    assert KioskPairing.objects.get(pk=pairing["id"]).status == KioskPairing.Status.PENDING

    saved = client.post(reverse("portal:profile_name"), {"full_name": "Ana Silva", "next": url}, follow=True)
    assert saved.context["done"] is True  # ao guardar o nome volta ao QR e liga
    assert KioskPairing.objects.get(pk=pairing["id"]).status == KioskPairing.Status.CLAIMED


def test_phone_login_on_kiosk_creates_account(screen, kiosk):
    sent = screen.post("/api/v1/kiosk/login/code", {"phone": "923456789"}, format="json").json()
    assert sent["sent"] is True and len(sent["dev_code"]) == 6
    wrong = screen.post(
        "/api/v1/kiosk/login/verify", {"phone": "923456789", "code": "000000"}, format="json"
    )
    assert wrong.status_code == 400
    ok = screen.post(
        "/api/v1/kiosk/login/verify",
        {"phone": "923456789", "code": sent["dev_code"], "language": "en"},
        format="json",
    ).json()
    user = User.objects.get(phone="+244923456789")
    assert ok["user"]["needs_name"] is True and user.language == "en"

    session = session_client(ok["token"])
    assert session.post("/api/v1/me", {"full_name": " "}, format="json").status_code == 400
    named = session.post("/api/v1/me", {"full_name": "joão  silva"}, format="json").json()
    assert named["name"] == "joão silva" and named["needs_name"] is False

    again = screen.post("/api/v1/kiosk/login/code", {"phone": "923456789"}, format="json")
    assert again.status_code == 429  # 1 código por minuto
    assert (
        screen.post("/api/v1/kiosk/login/code", {"phone": "12"}, format="json").status_code == 400
    )


def test_disabled_account_on_kiosk(screen):
    UserFactory(phone="+244923456780", is_active=False)
    code = screen.post("/api/v1/kiosk/login/code", {"phone": "923456780"}, format="json").json()
    response = screen.post(
        "/api/v1/kiosk/login/verify",
        {"phone": "923456780", "code": code["dev_code"]},
        format="json",
    )
    assert response.status_code == 403


def test_upload_qr(screen):
    data = screen.get("/api/v1/kiosk/upload-qr").json()
    assert data["url"].endswith("/ficheiros/") and data["qr_svg"].startswith("<svg")


@needs_tools
def test_print_from_my_files(screen, kiosk, s3, tmp_path, django_capture_on_commit_callbacks):
    user = UserFactory(full_name="Rui")
    path = make_pdf(tmp_path / "aula.pdf", [page(box="black"), page()])
    with open(path, "rb") as fh, django_capture_on_commit_callbacks(execute=True):
        library.save_document(user, fh, "aula.pdf", path.stat().st_size)
    Document.objects.create(
        owner=user, original_filename="velho.pdf", file_key="k", expires_at=timezone.now()
    )

    pairing = screen.post("/api/v1/kiosk/pairings").json()
    KioskPairing.objects.filter(pk=pairing["id"]).update(status="CLAIMED", user=user)
    token = screen.get(f"/api/v1/kiosk/pairings/{pairing['id']}").json()["token"]
    session = session_client(token)

    docs = session.get("/api/v1/documents").json()
    assert [d["filename"] for d in docs] == ["aula.pdf"]  # o expirado não aparece
    with django_capture_on_commit_callbacks(execute=True):
        created = session.post(
            f"/api/v1/kiosks/{kiosk.code}/jobs", {"document_id": docs[0]["id"]}, format="json"
        )
    assert created.status_code == 201
    job = session.get(f"/api/v1/jobs/{created.json()['id']}").json()
    assert (job["state"], job["pages"], job["total_kz"]) == (JobState.ANALYZED, 2, 200)

    stranger = UserFactory()
    other_doc = Document.objects.create(
        owner=stranger,
        original_filename="x.pdf",
        file_key="k2",
        state="READY",
        expires_at=timezone.now() + timedelta(days=1),
    )
    response = session.post(
        f"/api/v1/kiosks/{kiosk.code}/jobs", {"document_id": str(other_doc.id)}, format="json"
    )
    assert response.status_code == 404
    assert session.post(f"/api/v1/kiosks/{kiosk.code}/jobs", {}, format="json").status_code == 400
