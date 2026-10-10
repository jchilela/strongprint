import io
import re
import shutil
from datetime import timedelta

import httpx
import pytest
import respx
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from apps.accounts import otp
from apps.accounts.models import PhoneOTP, User
from apps.jobs import library, tasks
from apps.jobs.models import Document, Job
from apps.payments.models import TopUp
from apps.portal.templatetags.portal import kz, signed_kz
from apps.wallets import services as wallet
from common import storage
from domain.analysis.types import AnalysisError
from domain.jobs.state_machine import JobState
from tests.docs import make_pdf, page
from tests.factories import JobFactory, KioskFactory, UserFactory

pytestmark = pytest.mark.django_db
needs_tools = pytest.mark.skipif(not shutil.which("gs"), reason="needs ghostscript")


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


def shown_code(response) -> str:
    text = " ".join(str(m) for m in get_messages(response.wsgi_request))
    return re.search(r"(\d{6})", text).group(1)


def sign_in(client, phone="923456789"):
    response = client.post(reverse("portal:login"), {"phone": phone})
    assert response.status_code == 302, response.content
    code = shown_code(response)
    return client.post(reverse("portal:login_code"), {"code": code})


# --- entrada / registo ------------------------------------------------------------


def test_new_number_creates_account_then_asks_name(client):
    response = sign_in(client)
    assert response.url == reverse("portal:home")
    user = User.objects.get(phone="+244923456789")
    assert user.full_name == "" and user.phone_verified_at is not None
    assert not user.has_usable_password()
    assert wallet.wallet_for(user).balance_kz == 0

    home = client.get(reverse("portal:home")).content.decode()
    assert 'dialog class="modal" open' in home and "<main inert" in home

    # nada se pode fazer sem nome
    blocked = client.post(reverse("portal:topup_new"), {"amount_kz": 1000})
    assert blocked.url == reverse("portal:home")
    assert not TopUp.objects.exists()

    assert client.post(reverse("portal:profile_name"), {"full_name": " "}).status_code == 302
    user.refresh_from_db()
    assert user.full_name == ""
    client.post(
        reverse("portal:profile_name"), {"full_name": "  Ana   Silva ", "next": "/carteira/"}
    )
    user.refresh_from_db()
    assert user.full_name == "Ana Silva"
    page_ = client.get(reverse("portal:home")).content.decode()
    assert 'dialog class="modal"' not in page_ and "Ana Silva" in page_
    assert client.post(reverse("portal:topup_new"), {"amount_kz": 1000}).status_code == 302
    assert TopUp.objects.count() == 1


def test_disabled_account_cannot_sign_in(client):
    UserFactory(phone="+244923456789", is_active=False)
    response = sign_in(client)
    assert response.status_code == 200
    assert "_auth_user_id" not in client.session


def test_existing_customer_signs_in(client):
    UserFactory(phone="+244923456789", full_name="Rui")
    response = sign_in(client)
    assert response.url == reverse("portal:home")
    assert client.get(reverse("portal:wallet")).status_code == 200


def test_next_parameter_is_honoured_and_sanitised(client):
    UserFactory(phone="+244923456789")
    client.get(reverse("portal:login") + "?next=/ficheiros/")
    assert sign_in(client).url == "/ficheiros/"
    client.post(reverse("portal:logout"))
    cache.clear()  # limite de 1 código por minuto
    client.get(reverse("portal:login") + "?next=https://evil.example/")
    assert sign_in(client).url == reverse("portal:home")


def test_wrong_code_and_attempt_limit(client, settings):
    client.post(reverse("portal:login"), {"phone": "923456789"})
    for _ in range(settings.OTP_MAX_ATTEMPTS):
        response = client.post(reverse("portal:login_code"), {"code": "000000"})
        assert response.status_code == 200
    response = client.post(reverse("portal:login_code"), {"code": "000000"})
    assert (
        "Too many attempts" in response.content.decode()
        or "Demasiadas" in response.content.decode()
    )


def test_code_rate_limits(client):
    client.post(reverse("portal:login"), {"phone": "923456789"})
    again = client.post(reverse("portal:login"), {"phone": "923456789"})
    assert again.status_code == 200  # 1 por minuto
    assert PhoneOTP.objects.count() == 1


def test_invalid_phone(client):
    response = client.post(reverse("portal:login"), {"phone": "123"})
    assert response.status_code == 200
    assert not PhoneOTP.objects.exists()


def test_code_expiry_and_reuse(client):
    phone, code = otp.request_code("923456789")
    PhoneOTP.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    with pytest.raises(otp.OtpError) as exc:
        otp.verify_code(phone, code)
    assert exc.value.code == "expired"
    cache.clear()
    phone, code = otp.request_code("923456789")
    assert otp.verify_code(phone, code) is True
    with pytest.raises(otp.OtpError):
        otp.verify_code(phone, code)  # usado


def test_sms_text_is_plain_ascii():
    for language in ("pt", "en"):
        assert otp.message_for("123456", language).isascii()


def test_only_hash_is_stored():
    phone, code = otp.request_code("923456789")
    assert code not in PhoneOTP.objects.get().code_hash


def test_http_sender(settings):
    settings.OTP_SENDER = "http"
    settings.OTP_HTTP_URL = "http://sms.test/send"
    settings.OTP_HTTP_TOKEN = "t"
    settings.OTP_SHOW_CODE = False
    with respx.mock:
        route = respx.post("http://sms.test/send").mock(return_value=httpx.Response(200))
        phone, code = otp.request_code("923456789", language="en")
        assert code is None
        assert route.calls.last.request.headers["authorization"] == "Bearer t"
        assert "your code is" in route.calls.last.request.content.decode()
    cache.clear()
    with respx.mock:
        respx.post("http://sms.test/send").mock(return_value=httpx.Response(500))
        with pytest.raises(otp.OtpError) as exc:
            otp.request_code("923456780")
    assert exc.value.code == "send_failed"


def test_strongx_sender(settings):
    settings.OTP_SENDER = "strongx"
    settings.STRONGX_BASE_URL = "https://api.strongx.test/v1"
    settings.STRONGX_API_KEY = "strx_test_abc"
    settings.STRONGX_APPLICATION_ID = "app-1"
    settings.OTP_SHOW_CODE = False
    with respx.mock:
        route = respx.post("https://api.strongx.test/v1/sms/send").mock(
            return_value=httpx.Response(
                202, json={"success": True, "data": {"messageId": "m1", "status": "queued"}}
            )
        )
        phone, code = otp.request_code("923456789")
        sent = route.calls.last.request
        assert sent.headers["authorization"] == "Bearer strx_test_abc"
        payload = __import__("json").loads(sent.content)
        assert payload["to"] == "+244923456789" and payload["applicationId"] == "app-1"
        assert "StrongPrint" in payload["message"] and code is None
    cache.clear()
    with respx.mock:
        respx.post("https://api.strongx.test/v1/sms/send").mock(
            return_value=httpx.Response(402, json={"error": "insufficient_balance"})
        )
        with pytest.raises(otp.OtpError):
            otp.request_code("923456780")
    cache.clear()
    with respx.mock:
        respx.post("https://api.strongx.test/v1/sms/send").mock(
            side_effect=httpx.ConnectError("down")
        )
        with pytest.raises(otp.OtpError):
            otp.request_code("923456781")
    settings.STRONGX_API_KEY = ""
    cache.clear()
    with pytest.raises(otp.OtpError):
        otp.request_code("923456782")


def test_send_failure_falls_back_in_dev_mode(settings):
    settings.OTP_SENDER = "strongx"
    settings.STRONGX_BASE_URL = "https://api.strongx.test/v1"
    settings.STRONGX_API_KEY = "strx_test_abc"
    settings.OTP_SHOW_CODE = True
    with respx.mock:
        respx.post("https://api.strongx.test/v1/sms/send").mock(
            return_value=httpx.Response(422, json={"error": "sandbox_recipient_not_allowed"})
        )
        phone, code = otp.request_code("923456789")
    assert code and len(code) == 6


def test_resend_guards(client):
    assert client.post(reverse("portal:resend_code")).url == reverse("portal:login")
    assert client.get(reverse("portal:login_code")).url == reverse("portal:login")
    client.post(reverse("portal:login"), {"phone": "923456789"})
    response = client.post(reverse("portal:resend_code"))
    assert response.url == reverse("portal:login_code")  # 1/min: mostra erro e volta


def test_pages_require_login(client):
    for name in ("wallet", "topup_new", "documents", "history"):
        response = client.get(reverse(f"portal:{name}"))
        assert response.status_code == 302 and "/entrar/" in response.url
    assert client.get(reverse("portal:home")).status_code == 200  # página de apresentação


# --- carteira -------------------------------------------------------------------


@pytest.fixture
def customer_client(client):
    user = UserFactory(phone="+244923000111", full_name="Cliente")
    client.force_login(user)
    return client, user


def test_topup_flow_with_simulation(customer_client):
    client, user = customer_client
    response = client.post(reverse("portal:topup_new"), {"amount_kz": 2500})
    topup = TopUp.objects.get()
    assert response.url == reverse("portal:topup", args=[topup.pk])
    page_ = client.get(response.url).content.decode()
    assert topup.reference in page_ and 'http-equiv="refresh"' in page_
    client.post(reverse("portal:topup_simulate", args=[topup.pk]))
    topup.refresh_from_db()
    assert topup.status == TopUp.Status.PAID
    assert wallet.wallet_for(user).balance_kz == 2500
    assert "2 500" in client.get(reverse("portal:home")).content.decode()


def test_topup_validation_and_isolation(customer_client):
    client, _user = customer_client
    assert client.post(reverse("portal:topup_new"), {"amount_kz": 1}).status_code == 200
    other = TopUp.objects.create(
        wallet=wallet.wallet_for(UserFactory()),
        amount_kz=100,
        provider="mock",
        provider_payment_id="x",
        idempotency_key="x",
    )
    assert client.get(reverse("portal:topup", args=[other.pk])).status_code == 404


def test_topup_provider_down(customer_client, settings):
    client, _ = customer_client
    settings.PAYMENT_PROVIDER = "strongpay"
    settings.STRONGPAY_API_KEY = ""
    response = client.post(reverse("portal:topup_new"), {"amount_kz": 1000})
    assert response.status_code == 200
    assert not TopUp.objects.exists()


def test_simulation_disabled(customer_client, settings):
    client, user = customer_client
    client.post(reverse("portal:topup_new"), {"amount_kz": 1000})
    topup = TopUp.objects.get()
    settings.PAYMENTS_ALLOW_SIMULATION = False
    client.post(reverse("portal:topup_simulate", args=[topup.pk]))
    topup.refresh_from_db()
    assert topup.status == TopUp.Status.PENDING


# --- ficheiros --------------------------------------------------------------------


@needs_tools
def test_documents_upload_analyse_and_delete(
    customer_client, s3, tmp_path, django_capture_on_commit_callbacks
):
    client, user = customer_client
    path = make_pdf(tmp_path / "Trabalho.pdf", [page(), page(background="red")])
    with open(path, "rb") as fh, django_capture_on_commit_callbacks(execute=True):
        response = client.post(reverse("portal:documents"), {"file": fh})
    assert response.status_code == 302
    doc = Document.objects.get()
    assert (doc.state, doc.pages, doc.color_pages) == (Document.State.READY, 2, 1)
    assert storage.object_size(doc.document_key) > 0
    listing = client.get(reverse("portal:documents")).content.decode()
    assert "Trabalho.pdf" in listing and "200" in listing  # estimativa P&B 2 × 100

    client.post(reverse("portal:document_delete", args=[doc.pk]))
    assert not Document.objects.exists()
    assert storage.object_size(doc.file_key) is None


def test_document_rejections(customer_client, s3, django_capture_on_commit_callbacks):
    client, _ = customer_client
    bad = io.BytesIO(b"MZ")
    bad.name = "virus.exe"
    assert client.post(reverse("portal:documents"), {"file": bad}).status_code == 200
    broken = io.BytesIO(b"%PDF-1.4 broken")
    broken.name = "broken.pdf"
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("portal:documents"), {"file": broken})
    doc = Document.objects.get()
    assert doc.state == Document.State.REJECTED


def test_upload_size_limit(customer_client, s3, settings):
    client, _ = customer_client
    settings.UPLOAD_MAX_MB = 0
    upload = io.BytesIO(b"%PDF-1.4")
    upload.name = "a.pdf"
    assert client.post(reverse("portal:documents"), {"file": upload}).status_code == 200
    assert not Document.objects.exists()


@needs_tools
def test_job_from_library_document(
    customer_client, s3, tmp_path, django_capture_on_commit_callbacks
):
    client, user = customer_client
    path = make_pdf(tmp_path / "a.pdf", [page(box="black")])
    with open(path, "rb") as fh, django_capture_on_commit_callbacks(execute=True):
        doc = library.save_document(user, fh, "a.pdf", path.stat().st_size)
    doc.refresh_from_db()
    kiosk = KioskFactory()
    with django_capture_on_commit_callbacks(execute=True):
        job = library.job_from_document(doc, kiosk, actor="test")
    job.refresh_from_db()
    assert (job.state, job.pages, job.total_kz) == (JobState.ANALYZED, 1, 100)

    pending = Document.objects.create(
        owner=user, original_filename="x.pdf", file_key="k", expires_at=timezone.now()
    )
    with pytest.raises(AnalysisError):
        library.job_from_document(pending, kiosk, actor="test")


def test_purge_expired_documents(customer_client, s3):
    _, user = customer_client
    Document.objects.create(
        owner=user,
        original_filename="old.pdf",
        file_key="documents/x/original.pdf",
        expires_at=timezone.now() - timedelta(days=1),
    )
    assert tasks.purge_documents() == 1
    assert not Document.objects.exists()
    assert tasks.analyze_document("00000000-0000-0000-0000-000000000000") == "missing"


# --- histórico e idioma ---------------------------------------------------------------


def test_history_lists_own_jobs(customer_client):
    client, user = customer_client
    JobFactory(customer=user, state=JobState.COMPLETED, original_filename="mine.pdf", total_kz=300)
    JobFactory(original_filename="theirs.pdf")
    html = client.get(reverse("portal:history")).content.decode()
    assert "mine.pdf" in html and "theirs.pdf" not in html


def test_language_switch(customer_client):
    client, _ = customer_client
    client.post("/i18n/setlang/", {"language": "pt", "next": "/"})
    assert "Saldo disponível" in client.get(reverse("portal:home")).content.decode()
    client.post("/i18n/setlang/", {"language": "en", "next": "/"})
    assert "Available balance" in client.get(reverse("portal:home")).content.decode()


def test_kz_filters():
    assert kz(1900) == "1 900 Kz"
    assert kz(None) == "—" and kz("x") == "x"
    assert kz(-300) == "−300 Kz"
    assert signed_kz(500).startswith("+") and signed_kz("x") == "x"


def test_job_state_labels():
    assert str(Job.State.COMPLETED.label) in {"Printed", "Impresso"}
