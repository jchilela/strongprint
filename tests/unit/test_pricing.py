import pytest

from domain.analysis.types import DocumentAnalysis, PageAnalysis
from domain.pricing.service import (
    InvalidOptions,
    PriceRules,
    PrintOptions,
    classify_page,
    faces_value,
    price_job,
)

RULES = PriceRules(
    bw_page_kz=100,
    color_low_kz=200,
    color_mid_kz=350,
    color_high_kz=500,
    color_low_max_pct=10,
    color_mid_max_pct=30,
    min_transaction_kz=100,
)


def page(number, coverage, color=True):
    return PageAnalysis(
        number=number,
        cyan=0.1 if color else 0,
        magenta=0,
        yellow=0,
        black=0,
        color_pixels_pct=5.0 if color else 0.0,
        is_color=color,
        coverage_pct=coverage,
    )


DOC = DocumentAnalysis(pages=[page(1, 5, color=False), page(2, 5), page(3, 20), page(4, 45)])


@pytest.mark.parametrize(
    "coverage,kind",
    [
        (0, "color_low"),
        (9.99, "color_low"),
        (10, "color_mid"),
        (30, "color_mid"),
        (30.01, "color_high"),
    ],
)
def test_color_tiers(coverage, kind):
    assert classify_page(page(1, coverage), PrintOptions(color=True), RULES)[0] == kind


def test_bw_job_charges_everything_as_bw():
    result = price_job(DOC, PrintOptions(color=False), RULES)
    assert result["summary"] == {"bw": 4, "color_low": 0, "color_mid": 0, "color_high": 0}
    assert result["total_kz"] == 400


def test_color_job_charges_every_page_as_color():
    """Escolher cores cobra cores, mesmo nas páginas que só têm preto."""
    result = price_job(DOC, PrintOptions(color=True), RULES)
    assert [line["unit_kz"] for line in result["pages"]] == [200, 200, 350, 500]
    assert result["summary"] == {"bw": 0, "color_low": 2, "color_mid": 1, "color_high": 1}
    assert result["per_copy_kz"] == 1250
    assert result["total_kz"] == 1250


def test_copies_duplex_and_range():
    result = price_job(
        DOC, PrintOptions(color=True, copies=3, duplex=True, page_range="2-4"), RULES
    )
    assert result["selected_pages"] == 3
    assert result["per_copy_kz"] == 1050
    assert result["subtotal_kz"] == 3150
    assert (result["faces"], result["sheets"]) == (9, 6)


def test_minimum_transaction():
    rules = PriceRules(**{**RULES.__dict__, "min_transaction_kz": 500})
    result = price_job(DOC, PrintOptions(page_range="1"), rules)
    assert (result["subtotal_kz"], result["total_kz"], result["minimum_applied"]) == (
        100,
        500,
        True,
    )


def test_invalid_range_raises_invalid_options():
    with pytest.raises(InvalidOptions) as exc:
        price_job(DOC, PrintOptions(page_range="9"), RULES)
    assert exc.value.code == "invalid_page_range"


@pytest.mark.parametrize("raw", [{"copies": 0}, {"copies": 51}, {"copies": "x"}])
def test_options_validation(raw):
    with pytest.raises(InvalidOptions):
        PrintOptions.from_dict(raw)


def test_options_from_dict_defaults():
    assert PrintOptions.from_dict(None) == PrintOptions()
    assert PrintOptions.from_dict({"page_range": "  "}).page_range is None
    assert PrintOptions.from_dict({"copies": "2", "color": 1}).to_dict() == {
        "color": True,
        "duplex": False,
        "copies": 2,
        "page_range": None,
    }


def test_faces_value_for_partial_prints():
    breakdown = price_job(DOC, PrintOptions(color=True, copies=2), RULES)
    assert faces_value(breakdown, 0) == 0
    assert faces_value(breakdown, 2) == 400  # páginas 1 e 2 da 1.ª cópia
    assert faces_value(breakdown, 5) == 1250 + 200
    assert faces_value(breakdown, 8) == breakdown["total_kz"]
    assert faces_value(breakdown, 99) == breakdown["total_kz"]
    assert faces_value({}, 3) == 0


def test_copy_rules_use_copy_price_and_minimum():
    rules = PriceRules(
        bw_page_kz=100, color_low_kz=200, color_mid_kz=350, color_high_kz=500,
        color_low_max_pct=10, color_mid_max_pct=30, min_transaction_kz=100,
        copy_bw_page_kz=30, copy_min_transaction_kz=0,
    )
    copy = rules.for_copy()
    assert (copy.bw_page_kz, copy.min_transaction_kz, copy.color_low_kz) == (30, 0, 200)
    one_page = DocumentAnalysis(pages=[page(1, 5, color=False)])
    assert price_job(one_page, PrintOptions(color=False), copy)["total_kz"] == 30
    assert price_job(one_page, PrintOptions(color=False), rules)["total_kz"] == 100
