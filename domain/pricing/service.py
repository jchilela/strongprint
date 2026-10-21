"""PricingService: preço de um trabalho a partir da análise, opções e tabela efetiva.

Regras (Kz inteiros):
- cobra-se por face impressa (frente e verso = 2 faces);
- trabalho em P&B: cada face custa ``bw_page_kz``;
- trabalho a cores (escolha do cliente): cada face custa o escalão da sua cobertura,
  mesmo que a página só tenha preto: < low_max% → color_low; ≤ mid_max% → color_mid;
  acima → color_high;
- total = max(soma × cópias, mínimo por transação).
"""

import math
from dataclasses import dataclass, replace

from domain.analysis.page_range import parse_page_range
from domain.analysis.types import AnalysisError, DocumentAnalysis, PageAnalysis

MAX_COPIES = 50


class InvalidOptions(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class PriceRules:
    bw_page_kz: int
    color_low_kz: int
    color_mid_kz: int
    color_high_kz: int
    color_low_max_pct: int
    color_mid_max_pct: int
    min_transaction_kz: int
    copy_bw_page_kz: int = 30
    copy_min_transaction_kz: int = 0

    def for_copy(self) -> "PriceRules":
        """Preços aplicados a uma cópia: preto e mínimo próprios, cores iguais."""
        return replace(
            self,
            bw_page_kz=self.copy_bw_page_kz,
            min_transaction_kz=self.copy_min_transaction_kz,
        )


@dataclass(frozen=True)
class PrintOptions:
    color: bool = False
    duplex: bool = False
    copies: int = 1
    page_range: str | None = None

    @classmethod
    def from_dict(cls, data: dict | None) -> "PrintOptions":
        data = data or {}
        try:
            copies = int(data.get("copies", 1))
        except (TypeError, ValueError) as exc:
            raise InvalidOptions("invalid_copies", "Copies must be a number") from exc
        if not 1 <= copies <= MAX_COPIES:
            raise InvalidOptions("invalid_copies", f"Copies must be between 1 and {MAX_COPIES}")
        page_range = str(data.get("page_range") or "").strip() or None
        return cls(
            color=bool(data.get("color", False)),
            duplex=bool(data.get("duplex", False)),
            copies=copies,
            page_range=page_range,
        )

    def to_dict(self) -> dict:
        return {
            "color": self.color,
            "duplex": self.duplex,
            "copies": self.copies,
            "page_range": self.page_range,
        }


def classify_page(page: PageAnalysis, options: PrintOptions, rules: PriceRules) -> tuple[str, int]:
    if not options.color:
        return "bw", rules.bw_page_kz
    if page.coverage_pct < rules.color_low_max_pct:
        return "color_low", rules.color_low_kz
    if page.coverage_pct <= rules.color_mid_max_pct:
        return "color_mid", rules.color_mid_kz
    return "color_high", rules.color_high_kz


def price_job(analysis: DocumentAnalysis, options: PrintOptions, rules: PriceRules) -> dict:
    try:
        selected = parse_page_range(options.page_range, analysis.page_count)
    except AnalysisError as exc:
        raise InvalidOptions(exc.code, str(exc)) from exc

    by_number = {p.number: p for p in analysis.pages}
    lines = []
    summary = {"bw": 0, "color_low": 0, "color_mid": 0, "color_high": 0}
    for number in selected:
        page = by_number[number]
        kind, unit = classify_page(page, options, rules)
        summary[kind] += 1
        lines.append(
            {"page": number, "kind": kind, "coverage_pct": page.coverage_pct, "unit_kz": unit}
        )

    per_copy = sum(line["unit_kz"] for line in lines)
    subtotal = per_copy * options.copies
    total = max(subtotal, rules.min_transaction_kz)
    sheets_per_copy = math.ceil(len(selected) / 2) if options.duplex else len(selected)
    return {
        "currency": "AOA",
        "options": options.to_dict(),
        "pages": lines,
        "summary": summary,
        "selected_pages": len(selected),
        "copies": options.copies,
        "faces": len(selected) * options.copies,
        "sheets": sheets_per_copy * options.copies,
        "per_copy_kz": per_copy,
        "subtotal_kz": subtotal,
        "minimum_kz": rules.min_transaction_kz,
        "minimum_applied": total > subtotal,
        "total_kz": total,
    }


def faces_value(breakdown: dict, faces_printed: int) -> int:
    """Valor das primeiras ``faces_printed`` faces (pela ordem de impressão).

    Usado para debitar só o que saiu da impressora num trabalho PARTIAL.
    """
    pages = breakdown.get("pages", [])
    if not pages or faces_printed <= 0:
        return 0
    order = [line["unit_kz"] for line in pages] * int(breakdown.get("copies", 1))
    value = sum(order[:faces_printed])
    if faces_printed >= len(order):
        return int(breakdown.get("total_kz", value))
    return value
