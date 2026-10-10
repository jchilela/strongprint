"""Relatórios do parceiro: receita, trabalhos e páginas por período, por dia e por kiosk.

A receita vem dos débitos de impressão (LedgerEntry CAPTURE), que já estão
atribuídos ao kiosk onde se imprimiu.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.jobs.models import Job
from apps.wallets.models import LedgerEntry
from domain.jobs.state_machine import JobState

from .models import Kiosk

PERIODS = ("today", "7d", "30d", "month")
PRINTED = (JobState.COMPLETED, JobState.PARTIAL)


@dataclass(frozen=True)
class Period:
    key: str
    start: date
    end: date  # inclusivo

    @property
    def start_dt(self) -> datetime:
        return timezone.make_aware(datetime.combine(self.start, time.min))

    @property
    def end_dt(self) -> datetime:  # exclusivo
        return timezone.make_aware(datetime.combine(self.end + timedelta(days=1), time.min))

    @property
    def days(self) -> list[date]:
        return [self.start + timedelta(days=i) for i in range((self.end - self.start).days + 1)]


def period_from(key: str | None, start: str | None = None, end: str | None = None) -> Period:
    today = timezone.localdate()
    if start and end:
        try:
            a, b = date.fromisoformat(start), date.fromisoformat(end)
        except ValueError:
            a = b = None
        if a and b and a <= b and (b - a).days <= 366:
            return Period("custom", a, min(b, today))
    if key == "today":
        return Period(key, today, today)
    if key == "30d":
        return Period(key, today - timedelta(days=29), today)
    if key == "month":
        return Period(key, today.replace(day=1), today)
    return Period("7d", today - timedelta(days=6), today)


def _captures(kiosks, period: Period):
    return LedgerEntry.objects.filter(
        kind=LedgerEntry.Kind.CAPTURE,
        kiosk__in=kiosks,
        created_at__gte=period.start_dt,
        created_at__lt=period.end_dt,
    )


def _printed_jobs(kiosks, period: Period):
    return Job.objects.filter(
        kiosk__in=kiosks,
        state__in=PRINTED,
        finished_at__gte=period.start_dt,
        finished_at__lt=period.end_dt,
    )


def daily_revenue(kiosks, period: Period) -> list[dict]:
    rows = (
        _captures(kiosks, period)
        .annotate(day=TruncDate("created_at", tzinfo=timezone.get_current_timezone()))
        .values("day")
        .annotate(kz=-Sum("amount_kz"))
    )
    by_day = {r["day"]: r["kz"] for r in rows}
    series = [{"day": d, "kz": int(by_day.get(d, 0) or 0)} for d in period.days]
    peak = max((s["kz"] for s in series), default=0) or 1
    for s in series:
        s["pct"] = round(100 * s["kz"] / peak)
    return series


def summary(kiosks, period: Period) -> dict:
    captures = _captures(kiosks, period)
    totals = captures.aggregate(kz=Sum("amount_kz"), jobs=Count("job", distinct=True))
    faces = _printed_jobs(kiosks, period).aggregate(n=Sum("pages_printed"))["n"] or 0
    return {
        "revenue_kz": -(totals["kz"] or 0),
        "jobs": totals["jobs"] or 0,
        "faces": faces,
    }


def per_kiosk(kiosks, period: Period) -> list[dict]:
    revenue = {
        r["kiosk"]: r
        for r in _captures(kiosks, period)
        .values("kiosk")
        .annotate(kz=-Sum("amount_kz"), jobs=Count("job", distinct=True))
    }
    faces = {
        r["kiosk"]: r["n"]
        for r in _printed_jobs(kiosks, period).values("kiosk").annotate(n=Sum("pages_printed"))
    }
    rows = []
    for kiosk in kiosks.select_related("site", "printer__edge_device").annotate(
        open_alerts=Count("alerts", filter=Q(alerts__resolved_at__isnull=True))
    ):
        r = revenue.get(kiosk.pk, {})
        rows.append(
            {
                "kiosk": kiosk,
                "printer": getattr(kiosk, "printer", None),
                "revenue_kz": r.get("kz") or 0,
                "jobs": r.get("jobs") or 0,
                "faces": faces.get(kiosk.pk) or 0,
                "open_alerts": kiosk.open_alerts,
            }
        )
    return rows


def kiosks_for(partner):
    return Kiosk.objects.filter(site__partner=partner, is_active=True).order_by("code")


def mask_phone(phone: str) -> str:
    """+244923456789 → +244 9•• ••• 789 (os parceiros não veem dados dos clientes)."""
    if not phone or len(phone) < 7:
        return "—"
    return f"{phone[:4]} {phone[4]}•• ••• {phone[-3:]}"
