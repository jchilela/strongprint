"""Imprimir por e-mail.

Uma única caixa StrongPrint. Em cada e-mail, o **assunto é o número de telefone** do
cliente; os anexos vão para "Meus ficheiros" desse número (a conta é criada se ainda
não existir). Nada é impresso sem o cliente ir ao kiosk e pagar.
"""

import email
import imaplib
import io
import re
from datetime import timedelta
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr

import structlog
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils import timezone

from apps.accounts.phone import normalize_phone
from apps.wallets.services import wallet_for
from domain.analysis.analyzer import IMAGE_EXTENSIONS, SUPPORTED_EXTENSIONS, extension_of
from domain.analysis.types import AnalysisError

from . import library
from .models import Document, InboundEmail, Job

log = structlog.get_logger(__name__)
User = get_user_model()
S = InboundEmail.Status

# Logótipos e assinaturas coladas no corpo do e-mail não são ficheiros para imprimir.
MIN_IMAGE_BYTES = 15 * 1024
_PHONE_CHARS = re.compile(r"\+?[\d][\d\s.\-()]{6,}\d")


def enabled() -> bool:
    return bool(settings.INBOUND_IMAP_HOST and settings.INBOUND_IMAP_USER)


def decode(value) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(str(value)))).strip()
    except (UnicodeError, LookupError, email.errors.HeaderParseError):
        return str(value).strip()


def phone_from_subject(subject: str) -> str | None:
    """Primeiro número de telefone válido no assunto (aceita espaços, +244, pontos, traços)."""
    for match in _PHONE_CHARS.findall(subject or ""):
        digits = re.sub(r"[^\d+]", "", match)
        try:
            return normalize_phone(digits)
        except ValidationError:
            continue
    return None


def attachments(msg: Message) -> tuple[list[tuple[str, bytes]], list[str]]:
    """(ficheiros a guardar, nomes ignorados)."""
    files, skipped = [], []
    for part in msg.walk():
        if part.is_multipart():
            continue
        filename = decode(part.get_filename())
        if not filename:
            continue
        payload = part.get_payload(decode=True) or b""
        ext = extension_of(filename)
        inline = (part.get_content_disposition() or "") == "inline"
        if ext in IMAGE_EXTENSIONS and inline and len(payload) < MIN_IMAGE_BYTES:
            continue  # imagem da assinatura
        if ext not in SUPPORTED_EXTENSIONS:
            skipped.append(filename)
            continue
        files.append((filename, payload))
    return files, skipped


def customer_for(phone: str):
    user = User.objects.filter(phone=phone).first()
    if user is None:
        user = User.objects.create_user(phone, language="pt")
        log.info("inbound_email.customer_created", phone=phone[-4:])
    wallet_for(user)
    return user


def over_limit(phone: str) -> bool:
    since = timezone.now() - timedelta(hours=1)
    recent = InboundEmail.objects.filter(phone=phone, received_at__gte=since).count()
    return recent >= settings.INBOUND_MAX_PER_HOUR


def process_message(raw: bytes) -> InboundEmail | None:
    """Trata um e-mail (idempotente pelo Message-ID). Devolve None se já foi tratado."""
    msg = email.message_from_bytes(raw)
    sender = parseaddr(decode(msg.get("From")))[1][:254]
    subject = decode(msg.get("Subject"))[:255]
    message_id = (msg.get("Message-ID") or "").strip()[:255] or (
        f"no-id:{sender}:{msg.get('Date', '')}:{len(raw)}"
    )[:255]
    if InboundEmail.objects.filter(message_id=message_id).exists():
        return None

    record = InboundEmail(message_id=message_id, sender=sender, subject=subject)
    phone = phone_from_subject(subject)
    files, skipped = attachments(msg)
    record.skipped = skipped
    if phone is None:
        record.status = S.NO_PHONE
    else:
        record.phone = phone
        if over_limit(phone):
            record.status = S.RATE_LIMITED
        elif not files:
            record.status = S.NO_FILES
        else:
            record.customer = customer_for(phone)
            record.status = S.ACCEPTED
    try:
        record.save()
    except IntegrityError:
        return None  # outro processo tratou o mesmo e-mail

    if record.status == S.ACCEPTED:
        added = 0
        for filename, payload in files[: settings.INBOUND_MAX_ATTACHMENTS]:
            try:
                doc = library.save_document(
                    record.customer,
                    io.BytesIO(payload),
                    filename,
                    len(payload),
                    source=Job.Source.EMAIL,
                )
            except AnalysisError as exc:
                record.skipped.append(f"{filename} ({exc.code})")
                continue
            Document.objects.filter(pk=doc.pk).update(email_from=sender)
            added += 1
        record.skipped += [f for f, _ in files[settings.INBOUND_MAX_ATTACHMENTS :]]
        record.files = added
        if not added:
            record.status = S.NO_FILES
        record.save(update_fields=["files", "skipped", "status"])
    log.info(
        "inbound_email.processed",
        status=record.status,
        files=record.files,
        skipped=len(record.skipped),
        phone=record.phone[-4:],
    )
    return record


def fetch_mailbox(imap_factory=imaplib.IMAP4_SSL) -> int:
    """Lê os e-mails por ler, trata-os e apaga-os da caixa (os ficheiros ficam connosco)."""
    if not enabled():
        return 0
    if not cache.add("inbound_email:fetching", 1, 300):
        return 0  # outra leitura em curso
    count = 0
    try:
        imap = imap_factory(settings.INBOUND_IMAP_HOST, settings.INBOUND_IMAP_PORT)
        try:
            imap.login(settings.INBOUND_IMAP_USER, settings.INBOUND_IMAP_PASSWORD)
            imap.select(settings.INBOUND_IMAP_FOLDER)
            _, data = imap.uid("search", None, "UNSEEN")
            for uid in (data[0] or b"").split()[:50]:
                _, parts = imap.uid("fetch", uid, "(RFC822)")
                raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
                if raw is None:
                    continue
                try:
                    process_message(raw)
                except Exception:
                    # Fica por ler na caixa: tentamos de novo na próxima volta.
                    log.exception("inbound_email.failed", uid=uid.decode())
                    imap.uid("store", uid, "-FLAGS", "(\\Seen)")
                    continue
                imap.uid("store", uid, "+FLAGS", "(\\Deleted)")
                count += 1
            imap.expunge()
        finally:
            try:
                imap.logout()
            except Exception:
                pass
    finally:
        cache.delete("inbound_email:fetching")
    return count
