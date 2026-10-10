"""Números do painel do dono (superadmin).

- Vendas (receita) = débitos de impressão/cópia no livro-razão (LedgerEntry CAPTURE).
- Dinheiro recebido = carregamentos pagos (TopUp PAID).
- Carteiras = saldo que os clientes já pagaram e ainda não gastaram (responsabilidade).
"""

from datetime import datetime, time, timedelta

from django.db.models import Count, Max, Q, Sum
from django.utils import timezone

from apps.accounts.models import User
from apps.jobs.models import Job
from apps.monitoring.models import Alert, Downtime, format_duration
from apps.partners import reports
from apps.partners.models import Kiosk, Partner
from apps.payments.models import TopUp
from apps.wallets.models import LedgerEntry, Wallet
from common.auth import kiosk_activity
from domain.jobs.state_machine import JobState

ACTIVE_JOB_STATES = [
    JobState.CREATED,
    JobState.FILE_RECEIVED,
    JobState.ANALYZED,
    JobState.RESERVED,
    JobState.QUEUED,
    JobState.PRINTING,
]


def period_starts(now=None) -> dict[str, datetime]:
    now = timezone.localtime(now or timezone.now())
    today = timezone.make_aware(datetime.combine(now.date(), time.min))
    return {
        "today": today,
        "week": today - timedelta(days=now.weekday()),
        "month": today.replace(day=1),
        "year": today.replace(month=1, day=1),
        "all": None,
    }


def _captures(start=None):
    qs = LedgerEntry.objects.filter(kind=LedgerEntry.Kind.CAPTURE)
    return qs.filter(created_at__gte=start) if start else qs


def sales_summary() -> dict:
    out = {}
    for key, start in period_starts().items():
        captures = _captures(start).aggregate(kz=Sum("amount_kz"), jobs=Count("job", distinct=True))
        paid = TopUp.objects.filter(status=TopUp.Status.PAID)
        if start:
            paid = paid.filter(paid_at__gte=start)
        printed = Job.objects.filter(state__in=[JobState.COMPLETED, JobState.PARTIAL])
        if start:
            printed = printed.filter(finished_at__gte=start)
        out[key] = {
            "revenue_kz": -(captures["kz"] or 0),
            "jobs": captures["jobs"] or 0,
            "faces": printed.aggregate(n=Sum("pages_printed"))["n"] or 0,
            "copies": printed.filter(source=Job.Source.COPY).count(),
            "topups_kz": paid.aggregate(kz=Sum("paid_amount_kz"))["kz"] or 0,
            "topups": paid.count(),
        }
    return out


def wallet_summary() -> dict:
    totals = Wallet.objects.aggregate(balance=Sum("balance_kz"), held=Sum("held_kz"))
    month_start = period_starts()["month"]
    return {
        "balance_kz": totals["balance"] or 0,
        "held_kz": totals["held"] or 0,
        "available_kz": (totals["balance"] or 0) - (totals["held"] or 0),
        "with_balance": Wallet.objects.filter(balance_kz__gt=0).count(),
        "customers": Wallet.objects.count(),
        "new_customers_month": User.objects.filter(date_joined__gte=month_start).count(),
        "pending_topups": TopUp.objects.filter(status=TopUp.Status.PENDING).count(),
    }


def partner_rows() -> list[dict]:
    starts = period_starts()
    rows = []
    partners = Partner.objects.annotate(
        kiosks=Count("sites__kiosks", filter=Q(sites__kiosks__is_active=True), distinct=True),
        online=Count(
            "sites__kiosks",
            filter=Q(sites__kiosks__status=Kiosk.Status.ONLINE, sites__kiosks__is_active=True),
            distinct=True,
        ),
    ).order_by("name")
    for partner in partners:
        row = {"partner": partner, "kiosks": partner.kiosks, "online": partner.online}
        for key in ("today", "month", "year"):
            row[key] = -(
                _captures(starts[key])
                .filter(kiosk__site__partner=partner)
                .aggregate(kz=Sum("amount_kz"))["kz"]
                or 0
            )
        row["all"] = -(
            _captures().filter(kiosk__site__partner=partner).aggregate(kz=Sum("amount_kz"))["kz"]
            or 0
        )
        rows.append(row)
    return rows


def kiosk_rows() -> list[dict]:
    starts = period_starts()
    kiosks = list(
        Kiosk.objects.select_related("site__partner", "printer__edge_device")
        .annotate(open_alerts=Count("alerts", filter=Q(alerts__resolved_at__isnull=True)))
        .order_by("site__partner__name", "code")
    )
    ids = [k.pk for k in kiosks]
    activity = kiosk_activity(ids)
    active_jobs = {
        row["kiosk"]: row
        for row in Job.objects.filter(
            kiosk__in=ids,
            state__in=ACTIVE_JOB_STATES,
            updated_at__gte=timezone.now() - timedelta(minutes=15),
        )
        .values("kiosk")
        .annotate(n=Count("id"), last=Max("updated_at"))
    }
    printing = set(
        Job.objects.filter(kiosk__in=ids, state=JobState.PRINTING).values_list("kiosk", flat=True)
    )
    revenue = {}
    for key in ("today", "month"):
        for row in (
            _captures(starts[key])
            .filter(kiosk__in=ids)
            .values("kiosk")
            .annotate(kz=Sum("amount_kz"))
        ):
            revenue.setdefault(row["kiosk"], {})[key] = -row["kz"]

    availability = availability_7d(ids)
    rows = []
    for kiosk in kiosks:
        printer = getattr(kiosk, "printer", None)
        device = getattr(printer, "edge_device", None) if printer else None
        toner = (printer.status_detail or {}).get("toner", {}) if printer else {}
        levels = [v for v in toner.values() if isinstance(v, int) and v >= 0]
        rows.append(
            {
                "kiosk": kiosk,
                "partner": kiosk.site.partner,
                "printer": printer,
                "device": device,
                "toner": sorted(toner.items()),
                "toner_min": min(levels) if levels else None,
                "reasons": (printer.status_detail or {}).get("reasons", []) if printer else [],
                "in_use": kiosk.pk in activity or kiosk.pk in active_jobs,
                "printing": kiosk.pk in printing,
                "open_alerts": kiosk.open_alerts,
                "today": revenue.get(kiosk.pk, {}).get("today", 0),
                "month": revenue.get(kiosk.pk, {}).get("month", 0),
                "availability": availability.get(kiosk.pk),
            }
        )
    return rows


def daily_series(days: int = 30) -> list[dict]:
    today = timezone.localdate()
    period = reports.Period("custom", today - timedelta(days=days - 1), today)
    return reports.daily_revenue(Kiosk.objects.all(), period)


SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}


def open_alerts():
    alerts = Alert.objects.filter(resolved_at__isnull=True).select_related("kiosk")[:50]
    return sorted(
        alerts, key=lambda a: (SEVERITY_RANK.get(a.severity, 9), -a.opened_at.timestamp())
    )[:20]


def recent_jobs(limit: int = 12):
    return Job.objects.select_related("kiosk", "customer").order_by("-created_at")[:limit]


def recent_topups(limit: int = 10):
    return TopUp.objects.select_related("wallet__user").order_by("-created_at")[:limit]


def search_customers(query: str, limit: int = 20):
    query = (query or "").strip()
    if len(query) < 3:
        return []
    digits = "".join(c for c in query if c.isdigit())
    filters = Q(full_name__icontains=query)
    if digits:
        filters |= Q(phone__contains=digits[-9:])
    return list(User.objects.filter(filters).select_related("wallet").order_by("full_name")[:limit])


def top_wallets(limit: int = 10):
    return (
        Wallet.objects.select_related("user")
        .filter(balance_kz__gt=0)
        .order_by("-balance_kz")[:limit]
    )


def recent_downtimes(limit: int = 12):
    return Downtime.objects.select_related("kiosk").order_by("-started_at")[:limit]


def availability_7d(kiosk_ids) -> dict[int, dict]:
    """% do tempo disponível nos últimos 7 dias e tempo total indisponível, por kiosk."""
    now = timezone.now()
    start = now - timedelta(days=7)
    window = (now - start).total_seconds()
    lost: dict[int, float] = {}
    for d in Downtime.objects.filter(kiosk__in=kiosk_ids).filter(
        Q(ended_at__isnull=True) | Q(ended_at__gt=start)
    ):
        begin, end = max(d.started_at, start), d.ended_at or now
        lost[d.kiosk_id] = lost.get(d.kiosk_id, 0) + max((end - begin).total_seconds(), 0)
    return {
        pk: {
            "pct": round(100 * (1 - seconds / window), 1),
            "lost": format_duration(timedelta(seconds=seconds)),
        }
        for pk, seconds in lost.items()
    }
