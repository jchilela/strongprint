"""Portal do parceiro (partner.strongprint.it.ao).

Só entra quem o superadmin associou a um parceiro (PartnerMembership). Os dados
são sempre filtrados pelo parceiro escolhido; o responsável (OWNER) pode mudar o
nome/localização dos kiosks e pô-los em manutenção, quem só consulta (VIEWER) não.
"""

from functools import wraps

import structlog
from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model, logout
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_POST

from apps.accounts import otp, web
from apps.jobs.models import Job
from apps.monitoring.models import Alert
from apps.partners import reports
from apps.partners.models import Kiosk, Partner, PartnerMembership
from apps.portal.forms import CodeForm, PhoneForm

log = structlog.get_logger(__name__)
User = get_user_model()
SESSION_PHONE = "partner_otp_phone"
SESSION_PARTNER = "partner_id"


# --- acesso ---------------------------------------------------------------------


def accessible_partners(user):
    if not user.is_authenticated:
        return Partner.objects.none()
    if user.is_superuser:
        return Partner.objects.filter(is_active=True)
    return Partner.objects.filter(is_active=True, memberships__user=user).distinct()


def role_for(user, partner) -> str | None:
    if user.is_superuser:
        return PartnerMembership.Role.OWNER
    membership = PartnerMembership.objects.filter(user=user, partner=partner).first()
    return membership.role if membership else None


def partner_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('partner:login')}?next={request.get_full_path()}")
        partners = accessible_partners(request.user)
        partner = partners.filter(pk=request.session.get(SESSION_PARTNER)).first()
        partner = partner or partners.first()
        if partner is None:
            logout(request)
            messages.error(request, _("This account has no partner access."))
            return redirect("partner:login")
        request.session[SESSION_PARTNER] = partner.pk
        request.partner = partner
        request.partner_role = role_for(request.user, partner)
        request.partner_count = partners.count()
        request.is_owner = request.partner_role == PartnerMembership.Role.OWNER
        return view(request, *args, **kwargs)

    return wrapper


def base_context(request, **extra):
    return {
        "partner": request.partner,
        "is_owner": request.is_owner,
        "partners": accessible_partners(request.user) if request.partner_count > 1 else [],
        **extra,
    }


def login_phone(request):
    if request.user.is_authenticated and accessible_partners(request.user).exists():
        return redirect("partner:dashboard")
    if nxt := web.safe_next(request, request.GET.get("next")):
        request.session["partner_next"] = nxt
    form = PhoneForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        phone = form.cleaned_data["phone"]
        user = User.objects.filter(phone=phone, is_active=True).first()
        if user is None or not accessible_partners(user).exists():
            form.add_error("phone", _("This number has no partner access. Contact StrongPrint."))
        else:
            try:
                request.session[SESSION_PHONE] = web.send_code(request, phone)
            except otp.OtpError as exc:
                form.add_error("phone", web.otp_message(exc))
            else:
                return redirect("partner:login_code")
    return render(request, "partner/login_phone.html", {"form": form})


def login_code(request):
    phone = request.session.get(SESSION_PHONE)
    if not phone:
        return redirect("partner:login")
    form = CodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if error := web.check_code(phone, form.cleaned_data["code"]):
            form.add_error("code", error)
        else:
            request.session.pop(SESSION_PHONE, None)
            user = User.objects.get(phone=phone)
            log.info("partner.login", user_id=user.pk)
            return web.sign_in(
                request, user, request.session.pop("partner_next", None) or "partner:dashboard"
            )
    return render(request, "partner/login_code.html", {"form": form, "phone": phone})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("partner:login")


@partner_required
@require_POST
def switch_partner(request):
    partner = get_object_or_404(accessible_partners(request.user), pk=request.POST.get("partner"))
    request.session[SESSION_PARTNER] = partner.pk
    return redirect("partner:dashboard")


# --- painel ---------------------------------------------------------------------


def _period(request):
    return reports.period_from(
        request.GET.get("periodo"), request.GET.get("de"), request.GET.get("ate")
    )


@partner_required
def dashboard(request):
    period = _period(request)
    kiosks = reports.kiosks_for(request.partner)
    rows = reports.per_kiosk(kiosks, period)
    online = sum(1 for r in rows if r["kiosk"].status == Kiosk.Status.ONLINE)
    return render(
        request,
        "partner/dashboard.html",
        base_context(
            request,
            period=period,
            totals=reports.summary(kiosks, period),
            daily=reports.daily_revenue(kiosks, period),
            rows=rows,
            online=online,
            open_alerts=Alert.objects.filter(kiosk__in=kiosks, resolved_at__isnull=True).count(),
        ),
    )


class KioskForm(forms.ModelForm):
    class Meta:
        model = Kiosk
        fields = ["name", "location_hint"]
        labels = {
            "name": gettext_lazy("Display name"),
            "location_hint": gettext_lazy("Where is it?"),
        }


@partner_required
def kiosk_detail(request, code):
    kiosk = get_object_or_404(reports.kiosks_for(request.partner), code=code)
    form = KioskForm(request.POST or None, instance=kiosk)
    if request.method == "POST":
        if not request.is_owner:
            messages.error(request, _("Only the partner owner can change kiosks."))
            return redirect("partner:kiosk", code=code)
        if form.is_valid():
            form.save()
            log.info("partner.kiosk_edited", kiosk=kiosk.code, user_id=request.user.pk)
            messages.success(request, _("Kiosk updated."))
            return redirect("partner:kiosk", code=code)
    period = _period(request)
    single = Kiosk.objects.filter(pk=kiosk.pk)
    printer = getattr(kiosk, "printer", None)
    jobs = list(
        Job.objects.filter(kiosk=kiosk).select_related("customer").order_by("-created_at")[:20]
    )
    for job in jobs:
        job.masked_customer = reports.mask_phone(job.customer.phone)
    return render(
        request,
        "partner/kiosk.html",
        base_context(
            request,
            kiosk=kiosk,
            printer=printer,
            device=getattr(printer, "edge_device", None) if printer else None,
            form=form,
            period=period,
            totals=reports.summary(single, period),
            daily=reports.daily_revenue(single, period),
            alerts=kiosk.alerts.filter(resolved_at__isnull=True),
            jobs=jobs,
        ),
    )


@partner_required
@require_POST
def kiosk_status(request, code):
    kiosk = get_object_or_404(reports.kiosks_for(request.partner), code=code)
    if not request.is_owner:
        messages.error(request, _("Only the partner owner can change kiosks."))
        return redirect("partner:kiosk", code=code)
    action = request.POST.get("action")
    if action == "maintenance" and kiosk.status in (Kiosk.Status.ONLINE, Kiosk.Status.OFFLINE):
        kiosk.status = Kiosk.Status.MAINTENANCE
    elif action == "resume" and kiosk.status == Kiosk.Status.MAINTENANCE:
        kiosk.status = Kiosk.Status.OFFLINE  # volta a ONLINE no próximo sinal do Pi
    else:
        messages.error(request, _("This change is not possible now."))
        return redirect("partner:kiosk", code=code)
    Kiosk.objects.filter(pk=kiosk.pk).update(status=kiosk.status)
    log.info("partner.kiosk_status", kiosk=kiosk.code, status=kiosk.status, user_id=request.user.pk)
    messages.success(request, _("Kiosk status updated."))
    return redirect("partner:kiosk", code=code)


@partner_required
def jobs(request):
    kiosks = reports.kiosks_for(request.partner)
    qs = Job.objects.filter(kiosk__in=kiosks).select_related("kiosk", "customer")
    if code := request.GET.get("kiosk"):
        qs = qs.filter(kiosk__code=code)
    if state := request.GET.get("estado"):
        qs = qs.filter(state=state)
    page = list(qs.order_by("-created_at")[:100])
    for job in page:
        job.masked_customer = reports.mask_phone(job.customer.phone)
    return render(
        request,
        "partner/jobs.html",
        base_context(request, jobs=page, kiosks=kiosks, states=Job.State.choices),
    )


@partner_required
def alerts(request):
    kiosks = reports.kiosks_for(request.partner)
    return render(
        request,
        "partner/alerts.html",
        base_context(
            request,
            open_alerts=Alert.objects.filter(kiosk__in=kiosks, resolved_at__isnull=True)
            .select_related("kiosk")
            .order_by("-opened_at"),
            recent=Alert.objects.filter(kiosk__in=kiosks, resolved_at__isnull=False)
            .select_related("kiosk")
            .order_by("-resolved_at")[:30],
        ),
    )
