"""Portal do cliente (web, mobile-first, PT/EN)."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.accounts import otp, web
from apps.jobs import library
from apps.jobs.previews import preview_urls
from apps.jobs.models import Document, Job
from apps.partners.models import PriceTable
from apps.payments import services as payments
from apps.payments.models import TopUp
from apps.wallets.services import wallet_for
from domain.analysis.types import AnalysisError
from domain.payments.provider import PaymentProviderError

from .forms import CodeForm, NameForm, PhoneForm, TopUpForm, UploadForm

User = get_user_model()
SESSION_PHONE = "portal_otp_phone"
SESSION_NEXT = "portal_next"


def needs_name(user) -> bool:
    return user.is_authenticated and not (user.full_name or "").strip()


# --- autenticação -------------------------------------------------------------


def home(request):
    if not request.user.is_authenticated:
        return render(request, "portal/landing.html")
    wallet = wallet_for(request.user)
    return render(
        request,
        "portal/home.html",
        {
            "wallet": wallet,
            "entries": wallet.entries.select_related("kiosk")[:8],
            "pending_topups": wallet.topups.filter(status=TopUp.Status.PENDING)[:3],
            "recent_jobs": request.user.print_jobs.select_related("kiosk")[:3],
        },
    )


def login_phone(request):
    if request.user.is_authenticated:
        return redirect("portal:home")
    if nxt := web.safe_next(request, request.GET.get("next")):
        request.session[SESSION_NEXT] = nxt
    form = PhoneForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            request.session[SESSION_PHONE] = web.send_code(request, form.cleaned_data["phone"])
        except otp.OtpError as exc:
            form.add_error("phone", web.otp_message(exc))
        else:
            return redirect("portal:login_code")
    return render(request, "portal/login_phone.html", {"form": form})


def login_code(request):
    """Código certo → entra. Número novo → a conta é criada aqui; o nome é pedido depois."""
    phone = request.session.get(SESSION_PHONE)
    if not phone:
        return redirect("portal:login")
    form = CodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if error := web.check_code(phone, form.cleaned_data["code"]):
            form.add_error("code", error)
        else:
            request.session.pop(SESSION_PHONE, None)
            user = User.objects.filter(phone=phone).first()
            if user is None:
                user = User.objects.create_user(
                    phone,
                    language=translation.get_language() or "pt",
                    phone_verified_at=timezone.now(),
                )
            elif not user.is_active:
                form.add_error("code", _("This account is disabled."))
                return render(request, "portal/login_code.html", {"form": form, "phone": phone})
            wallet_for(user)
            return web.sign_in(
                request, user, request.session.pop(SESSION_NEXT, None) or "portal:home"
            )
    return render(request, "portal/login_code.html", {"form": form, "phone": phone})


@require_POST
def resend_code(request):
    phone = request.session.get(SESSION_PHONE)
    if not phone:
        return redirect("portal:login")
    try:
        web.send_code(request, phone)
    except otp.OtpError as exc:
        messages.error(request, web.otp_message(exc))
    else:
        messages.success(request, _("We sent you a new code."))
    return redirect("portal:login_code")


@login_required
@require_POST
def profile_name(request):
    """Guarda o nome pedido na janela obrigatória."""
    form = NameForm(request.POST)
    if form.is_valid():
        request.user.full_name = form.cleaned_data["full_name"]
        request.user.save(update_fields=["full_name"])
        messages.success(request, _("Welcome to StrongPrint!"))
    else:
        messages.error(request, _("Please enter your name."))
    return redirect(web.safe_next(request, request.POST.get("next")) or "portal:home")


@require_POST
def logout_view(request):
    logout(request)
    return redirect("portal:home")


# --- carteira ------------------------------------------------------------------


@login_required
def wallet(request):
    w = wallet_for(request.user)
    return render(
        request,
        "portal/wallet.html",
        {
            "wallet": w,
            "entries": w.entries.select_related("kiosk")[:50],
            "topups": w.topups.all()[:20],
        },
    )


@login_required
def topup_new(request):
    form = TopUpForm(request.POST or None, initial={"amount_kz": 1000})
    if request.method == "POST" and form.is_valid():
        try:
            topup = payments.create_topup(
                request.user,
                form.cleaned_data["amount_kz"],
                actor=f"portal:{request.user.pk}",
                idempotency_key=request.POST.get("idempotency_key") or None,
            )
        except PaymentProviderError:
            form.add_error(None, _("The payment service is unavailable. Try again shortly."))
        else:
            return redirect("portal:topup", topup_id=topup.pk)
    return render(request, "portal/topup_new.html", {"form": form, "presets": TopUpForm.PRESETS})


@login_required
def topup_detail(request, topup_id):
    topup = get_object_or_404(TopUp, pk=topup_id, wallet__user=request.user)
    return render(
        request,
        "portal/topup_detail.html",
        {
            "topup": topup,
            "can_simulate": topup.provider == "mock" and settings.PAYMENTS_ALLOW_SIMULATION,
        },
    )


@login_required
@require_POST
def topup_simulate(request, topup_id):
    topup = get_object_or_404(TopUp, pk=topup_id, wallet__user=request.user)
    try:
        payments.simulate_payment(topup, "paid")
    except PermissionError:
        messages.error(request, _("Simulation is not available."))
    return redirect("portal:topup", topup_id=topup.pk)


# --- ficheiros -----------------------------------------------------------------


def bw_estimate(pages: int | None) -> int | None:
    table = PriceTable.objects.filter(partner=None, site=None, kiosk=None).first()
    if not pages or table is None:
        return None
    return max(pages * table.bw_page_kz, table.min_transaction_kz)


@login_required
def documents(request):
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        upload = form.cleaned_data["file"]
        try:
            library.save_document(request.user, upload, upload.name, upload.size)
        except AnalysisError as exc:
            form.add_error(
                "file",
                {
                    "unsupported_format": _("This file type is not supported."),
                    "file_too_large": _("The file is too large (max %(mb)s MB).")
                    % {"mb": settings.UPLOAD_MAX_MB},
                }.get(exc.code, str(exc)),
            )
        else:
            messages.success(request, _("File saved. We are checking it now."))
            return redirect("portal:documents")
    docs = list(request.user.documents.all())
    printed = library.printed_info(docs)
    for doc in docs:
        doc.printed = printed.get(doc.pk)
        doc.estimate_kz = bw_estimate(doc.pages)
        doc.thumb = (preview_urls(doc) or [None])[0]
    return render(
        request,
        "portal/documents.html",
        {
            "form": form,
            "documents": docs,
            "max_mb": settings.UPLOAD_MAX_MB,
            "retention_days": settings.LIBRARY_RETENTION_DAYS,
            "inbound_email": settings.INBOUND_EMAIL_ADDRESS,
            "my_phone": request.user.phone.removeprefix("+244"),
            "pending": any(d.state == Document.State.PENDING for d in docs),
        },
    )


@login_required
@require_POST
def document_delete(request, document_id):
    doc = get_object_or_404(Document, pk=document_id, owner=request.user)
    library.delete_document(doc)
    messages.success(request, _("File deleted."))
    return redirect("portal:documents")


# --- impressões ------------------------------------------------------------------


@login_required
def document_view(request, document_id):
    doc = get_object_or_404(Document, pk=document_id, owner=request.user)
    return render(
        request,
        "portal/preview.html",
        {"doc": doc, "pages": preview_urls(doc), "back": "portal:documents"},
    )


@login_required
def history(request):
    from apps.wallets.models import WalletHold

    jobs = list(request.user.print_jobs.select_related("kiosk__site__partner")[:50])
    charged = dict(
        WalletHold.objects.filter(job__in=jobs).values_list("job_id", "captured_kz")
    )
    for job in jobs:
        job.thumb = (preview_urls(job) or [None])[0]
        # Impressão interrompida (erro da impressora): só se cobram as faces que saíram.
        job.interrupted = job.state in (Job.State.PARTIAL, Job.State.FAILED) and (
            job.failure_reason == "printer_error" or job.state == Job.State.PARTIAL
        )
        job.charged_kz = charged.get(job.pk, 0)
        job.refunded_kz = max(0, (job.total_kz or 0) - job.charged_kz)
    return render(request, "portal/history.html", {"jobs": jobs, "States": Job.State})


@login_required
def job_view(request, job_id):
    job = get_object_or_404(
        Job.objects.select_related("kiosk__site__partner"), pk=job_id, customer=request.user
    )
    return render(
        request,
        "portal/preview.html",
        {
            "job": job,
            "pages": preview_urls(job),
            "back": "portal:history",
            "retention_days": settings.PREVIEW_RETENTION_DAYS,
        },
    )


# --- ligar ao kiosk (QR do ecrã) ------------------------------------------------


@login_required
def kiosk_pair(request, code):
    """Ler o QR do ecrã = entrar nesse kiosk. Não há botão a confirmar: quem lê o QR
    está à frente do kiosk. Só liga com sessão aberta no telemóvel (o login vem antes),
    por isso pré-visualizações de links (sem sessão) nunca ligam ninguém."""
    from apps.devices.models import KioskPairing

    pairing = KioskPairing.objects.select_related("kiosk__site__partner").filter(
        code=code.upper()
    ).first()
    template = "portal/kiosk_pair.html"
    if pairing is not None and pairing.user_id == request.user.pk and not pairing.is_open:
        return render(request, template, {"pairing": pairing, "done": True})  # recarregou
    if pairing is None or not pairing.is_open:
        return render(request, template, {"pairing": None}, status=410)
    if needs_name(request.user):
        # O pedido do nome aparece por cima; ao guardar volta aqui e liga.
        return render(request, template, {"pairing": pairing, "waiting_name": True})
    updated = KioskPairing.objects.filter(
        pk=pairing.pk, status=KioskPairing.Status.PENDING
    ).update(status=KioskPairing.Status.CLAIMED, user=request.user, claimed_at=timezone.now())
    return render(request, template, {"pairing": pairing, "done": bool(updated)})
