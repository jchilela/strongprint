"""Decide o resultado de um trabalho comparando o contador da impressora com o que foi pago."""

from dataclasses import dataclass

from domain.jobs.state_machine import JobState
from domain.pricing.service import faces_value

from .gateway import JobOutcome


@dataclass(frozen=True)
class Verdict:
    state: JobState
    faces_printed: int
    capture_kz: int
    source: str  # "counter" | "ipp" | "none"
    overflow: int = 0  # faces a mais no contador (uso fora do StrongPrint?)

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "faces_printed": self.faces_printed,
            "capture_kz": self.capture_kz,
            "source": self.source,
            "overflow": self.overflow,
        }


def blank_faces(breakdown: dict) -> int:
    pages = breakdown.get("pages", [])
    return sum(1 for p in pages if p.get("coverage_pct", 1) == 0) * int(breakdown.get("copies", 1))


def decide(
    breakdown: dict,
    *,
    counter_before: int | None,
    counter_after: int | None,
    outcome: JobOutcome,
    impressions: int | None,
) -> Verdict:
    expected = int(breakdown.get("faces", 0))
    if counter_before is not None and counter_after is not None:
        raw, source = max(0, counter_after - counter_before), "counter"
    elif impressions is not None:
        raw, source = max(0, int(impressions)), "ipp"
    elif outcome == JobOutcome.COMPLETED:
        raw, source = expected, "ipp"
    else:
        raw, source = 0, "none"

    printed = min(raw, expected)
    overflow = max(0, raw - expected)
    # Algumas impressoras não contam páginas totalmente em branco.
    tolerance = blank_faces(breakdown) if outcome == JobOutcome.COMPLETED else 0

    if expected and printed >= expected - tolerance:
        return Verdict(JobState.COMPLETED, printed, int(breakdown["total_kz"]), source, overflow)
    if printed > 0:
        return Verdict(JobState.PARTIAL, printed, faces_value(breakdown, printed), source, overflow)
    return Verdict(JobState.FAILED, 0, 0, source, overflow)
