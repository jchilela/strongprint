"""Imprimir por e-mail: assunto = telefone, anexos → "Meus ficheiros"."""

from email.message import EmailMessage

import pytest

from apps.accounts.models import User
from apps.jobs import inbound_email
from apps.jobs.inbound_email import phone_from_subject, process_message
from apps.jobs.models import Document, InboundEmail
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db
S = InboundEmail.Status
PDF = b"%PDF-1.4 fake"


def mail(subject, attachments=(), msg_id="<a1@example.com>", sender="Joao <joao@example.com>"):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"], msg["Message-ID"] = (
        sender,
        "imprimir@strongprint.it.ao",
        subject,
        msg_id,
    )
    msg.set_content("Olá, segue o ficheiro.")
    for name, data, maintype, subtype, disposition in attachments:
        msg.add_attachment(
            data, maintype=maintype, subtype=subtype, filename=name, disposition=disposition
        )
    return msg.as_bytes()


def pdf(name="trabalho.pdf"):
    return (name, PDF, "application", "pdf", "attachment")


@pytest.mark.parametrize(
    "subject, phone",
    [
        ("931405768", "+244931405768"),
        ("+244 931 405 768", "+244931405768"),
        ("Re: Fwd: 931-405-768 imprimir", "+244931405768"),
        ("931.405.768", "+244931405768"),
        ("imprimir isto", None),
        ("123", None),
        ("", None),
    ],
)
def test_phone_from_subject(subject, phone):
    assert phone_from_subject(subject) == phone


def test_files_go_to_existing_customer(s3, django_capture_on_commit_callbacks):
    customer = UserFactory(phone="+244931405768")
    logo = ("image001.png", b"\x89PNG" + b"0" * 500, "image", "png", "inline")
    photo = ("foto.jpg", b"\xff\xd8" + b"0" * 30_000, "image", "jpeg", "attachment")
    zipped = ("coisas.zip", b"PK..", "application", "zip", "attachment")
    raw = mail("931 405 768", [pdf(), logo, photo, zipped])
    with django_capture_on_commit_callbacks():
        record = process_message(raw)
    assert (record.status, record.files, record.customer) == (S.ACCEPTED, 2, customer)
    assert record.skipped == ["coisas.zip"]
    docs = Document.objects.filter(owner=customer).order_by("original_filename")
    assert [d.original_filename for d in docs] == ["foto.jpg", "trabalho.pdf"]
    assert {d.source for d in docs} == {"EMAIL"}
    assert {d.email_from for d in docs} == {"joao@example.com"}

    assert process_message(raw) is None  # o mesmo e-mail não entra duas vezes
    assert Document.objects.filter(owner=customer).count() == 2


def test_unknown_phone_creates_customer(s3, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks():
        record = process_message(mail("923456789", [pdf()]))
    user = User.objects.get(phone="+244923456789")
    assert record.customer == user and user.documents.count() == 1
    assert hasattr(user, "wallet")


def test_rejections(s3, settings, django_capture_on_commit_callbacks):
    assert process_message(mail("olá", [pdf()], "<b1@x>")).status == S.NO_PHONE
    assert process_message(mail("931405768", [], "<b2@x>")).status == S.NO_FILES
    settings.INBOUND_MAX_PER_HOUR = 1
    with django_capture_on_commit_callbacks():
        assert process_message(mail("931405768", [pdf()], "<b3@x>")).status == S.RATE_LIMITED
    assert not Document.objects.exists()


def test_attachment_limit(s3, settings, django_capture_on_commit_callbacks):
    settings.INBOUND_MAX_ATTACHMENTS = 2
    with django_capture_on_commit_callbacks():
        record = process_message(mail("931405768", [pdf(f"f{i}.pdf") for i in range(4)]))
    assert record.files == 2 and record.skipped == ["f2.pdf", "f3.pdf"]


class FakeImap:
    def __init__(self, messages):
        self.messages = messages
        self.deleted, self.unseen = [], []

    def login(self, user, password):
        pass

    def select(self, folder):
        pass

    def uid(self, command, *args):
        if command == "search":
            return "OK", [b" ".join(self.messages)]
        if command == "fetch":
            return "OK", [(b"1 (RFC822)", self.messages[args[0]]), b")"]
        if command == "store":
            (self.deleted if args[1] == "+FLAGS" else self.unseen).append(args[0])
            return "OK", []

    def expunge(self):
        pass

    def logout(self):
        pass


def test_fetch_mailbox(s3, settings, django_capture_on_commit_callbacks):
    settings.INBOUND_IMAP_HOST, settings.INBOUND_IMAP_USER = "imap.example.com", "imprimir"
    imap = FakeImap({b"7": mail("931405768", [pdf()]), b"8": mail("sem número", [], "<c2@x>")})
    with django_capture_on_commit_callbacks():
        assert inbound_email.fetch_mailbox(lambda host, port: imap) == 2
    assert sorted(imap.deleted) == [b"7", b"8"]
    assert InboundEmail.objects.count() == 2


def test_fetch_disabled_without_settings(settings):
    settings.INBOUND_IMAP_HOST = ""
    assert inbound_email.fetch_mailbox(lambda *a: pytest.fail("should not connect")) == 0


def test_portal_shows_address_and_sender(client, settings, s3, django_capture_on_commit_callbacks):
    settings.INBOUND_EMAIL_ADDRESS = "imprimir@strongprint.it.ao"
    customer = UserFactory(phone="+244931405768", full_name="Julio")
    with django_capture_on_commit_callbacks():
        process_message(mail("931405768", [pdf()]))
    client.force_login(customer)
    page = client.get("/ficheiros/").content.decode()
    assert "imprimir@strongprint.it.ao" in page and "931405768" in page
    assert "joao@example.com" in page


def test_mailbox_login_failure_is_flagged_then_cleared(settings, s3):
    import imaplib

    from django.core.cache import cache

    settings.INBOUND_IMAP_HOST, settings.INBOUND_IMAP_USER = "imap.example.com", "imprimir"
    cache.delete(inbound_email.HEALTH_KEY)

    class Refused(FakeImap):
        def login(self, user, password):
            raise imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] Invalid credentials (Failure)")

    assert inbound_email.fetch_mailbox(lambda host, port: Refused({})) == 0
    problem = inbound_email.health()
    assert problem and "AUTHENTICATIONFAILED" in problem["error"]

    assert inbound_email.fetch_mailbox(lambda host, port: FakeImap({})) == 0
    assert inbound_email.health() is None


def test_dashboard_shows_mail_problem(settings, s3, client):
    from django.core.cache import cache
    from django.utils import timezone

    settings.INBOUND_IMAP_HOST, settings.INBOUND_IMAP_USER = "imap.example.com", "imprimir"
    cache.set(inbound_email.HEALTH_KEY, {"error": "Invalid credentials", "since": timezone.now().isoformat()})
    owner = UserFactory(is_staff=True, is_superuser=True)
    client.force_login(owner)
    page = client.get("/admin/").content.decode()
    assert "Invalid credentials" in page
    cache.delete(inbound_email.HEALTH_KEY)
