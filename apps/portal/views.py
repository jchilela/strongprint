"""Portal do cliente (web, mobile-first, PT/EN)."""

from datetime import datetime, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone, translation
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.accounts import otp
from apps.jobs import library
from apps.jobs.models import Document, Job
from apps.partners.models import PriceTable
from apps.payments import services as payments
from apps.payments.models import TopUp
from apps.wallets.services import wallet_for
from domain.analysis.types import AnalysisError
from domain.payments.provider import PaymentProviderError

from .forms import CodeForm, PhoneForm, RegisterForm, TopUpForm, UploadForm

User = get_user_model()
SESSION_PHONE = "portal_otp_phone"
SESSION_VERIFIED = "portal_verified_phone"
SESSION_NEXT = "portal_next"
VERIFIED_TTL = timedelta(minutes=15)


def client_ip(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return forwarded.split(",")[0].strip() or request.META.get("REMOTE_ADDR", "")


def safe_next(request, url: str | None) -> str | None:
    if url and url_has_allowed_host_and_scheme(url, {request.get_host()}):
        return url
    return None


def finish_login(request, user):
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    translation.activate(user.language)
    response = redirect(request.session.pop(SESSION_NEXT, None) or "portal:home")
    response.set_cookie(settings.LANGUAGE_COOKIE_NAME, user.language, max_age=365 * 86400)
    return response


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
    if nxt := safe_next(request, request.GET.get("next")):
        request.session[SESSION_NEXT] = nxt
    form = PhoneForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            phone, code = otp.request_code(
                form.cleaned_data["phone"],
                ip=client_ip(request),
                language=translation.get_language() or "pt",
            )
        except otp.OtpError as exc:
            form.add_error("phone", _otp_message(exc))
        else:
            request.session[SESSION_PHONE] = phone
            if code:
                messages.info(
                    request, _("Development mode — your code is %(code)s") % {"code": code}
                )
            return redirect("portal:login_code")
    return render(request, "portal/login_phone.html", {"form": form})


def login_code(request):
    phone = request.session.get(SESSION_PHONE)
    if not phone:
        return redirect("portal:login")
    form = CodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            ok = otp.verify_code(phone, form.cleaned_data["code"])
        except otp.OtpError as exc:
            form.add_error("code", _otp_message(exc))
        else:
            if not ok:
                form.add_error("code", _("Wrong code. Try again."))
            else:
                request.session.pop(SESSION_PHONE, None)
                user = User.objects.filter(phone=phone).first()
                if user is not None:
                    if not user.is_active:
                        form.add_error("code", _("This account is disabled."))
                        return render(
                            request, "portal/login_code.html", {"form": form, "phone": phone}
                        )
                    if user.phone_verified_at is None:
                        user.phone_verified_at = timezone.now()
                        user.save(update_fields=["phone_verified_at"])
                    return finish_login(request, user)
                request.session[SESSION_VERIFIED] = {
                    "phone": phone,
                    "at": timezone.now().isoformat(),
                }
                return redirect("portal:register")
    return render(request, "portal/login_code.html", {"form": form, "phone": phone})


@require_POST
def resend_code(request):
    phone = request.session.get(SESSION_PHONE)
    if not phone:
        return redirect("portal:login")
    try:
        _phone, code = otp.request_code(
            phone, ip=client_ip(request), language=translation.get_language() or "pt"
        )
    except otp.OtpError as exc:
        messages.error(request, _otp_message(exc))
    else:
        messages.success(request, _("We sent you a new code."))
        if code:
            messages.info(request, _("Development mode — your code is %(code)s") % {"code": code})
    return redirect("portal:login_code")


def register(request):
    verified = request.session.get(SESSION_VERIFIED)
    if not verified or timezone.now() - datetime.fromisoformat(verified["at"]) > VERIFIED_TTL:
        request.session.pop(SESSION_VERIFIED, None)
        return redirect("portal:login")
    form = RegisterForm(
        request.POST or None, initial={"language": translation.get_language() or "pt"}
    )
    if request.method == "POST" and form.is_valid():
        user = User.objects.filter(phone=verified["phone"]).first() or User.objects.create_user(
            verified["phone"],
            full_name=form.cleaned_data["full_name"],
            language=form.cleaned_data["language"],
            phone_verified_at=timezone.now(),
        )
        request.session.pop(SESSION_VERIFIED, None)
        wallet_for(user)
        return finish_login(request, user)
    return render(request, "portal/register.html", {"form": form, "phone": verified["phone"]})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("portal:home")


def _otp_message(exc: otp.OtpError) -> str:
    return {
        "too_soon": _("Please wait a minute before asking for a new code."),
        "too_many": _("Too many codes requested. Try again later."),
        "expired": _("The code expired. Ask for a new one."),
        "too_many_attempts": _("Too many attempts. Ask for a new code."),
        "send_failed": _("We could not send the code. Try again."),
    }.get(exc.code, str(exc))


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
    for doc in docs:
        doc.estimate_kz = bw_estimate(doc.pages)
    return render(
        request,
        "portal/documents.html",
        {
            "form": form,
            "documents": docs,
            "max_mb": settings.UPLOAD_MAX_MB,
            "retention_days": settings.LIBRARY_RETENTION_DAYS,
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
def history(request):
    jobs = request.user.print_jobs.select_related("kiosk__site__partner")[:50]
    return render(request, "portal/history.html", {"jobs": jobs, "States": Job.State})
