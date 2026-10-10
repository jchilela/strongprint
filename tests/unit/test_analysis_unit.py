import pytest

from domain.analysis.analyzer import DocumentAnalyzer, extension_of, parse_inkcov
from domain.analysis.page_range import parse_page_range
from domain.analysis.types import AnalysisError, PageAnalysis

INKCOV = """ 0.00000  0.00000  0.00000  0.04213 CMYK OK
 0.10000  0.20000  0.05000  0.01000 CMYK OK
garbage line
 0.00000  0.00000  0.00000  0.00000 CMYK OK
"""


def test_parse_inkcov():
    assert parse_inkcov(INKCOV) == [
        (0.0, 0.0, 0.0, 0.04213),
        (0.1, 0.2, 0.05, 0.01),
        (0.0, 0.0, 0.0, 0.0),
    ]


def test_page_measured_bw_and_color():
    # preto rico (C≈M≈Y≈K) sem píxeis coloridos → P&B; cobertura = canal mais alto
    rich_black = PageAnalysis.measured(1, (0.2506, 0.252, 0.252, 0.2485), 0.0, 0.1)
    assert (rich_black.is_color, rich_black.coverage_pct) == (False, 25.2)
    color = PageAnalysis.measured(2, (0.003, 0.0224, 0.0234, 0.001), 2.1, 0.1)
    assert (color.is_color, color.coverage_pct) == (True, 2.34)
    capped = PageAnalysis.measured(3, (1, 1, 1, 1), 100, 0.1)
    assert capped.coverage_pct == 100.0
    # poucos píxeis coloridos (ruído) abaixo do limiar → P&B
    assert not PageAnalysis.measured(4, (0, 0, 0, 0.3), 0.05, 0.1).is_color


@pytest.mark.parametrize(
    "spec,expected",
    [
        (None, [1, 2, 3, 4, 5]),
        ("", [1, 2, 3, 4, 5]),
        ("2", [2]),
        ("1-3", [1, 2, 3]),
        ("1-2, 4", [1, 2, 4]),
        ("4,1-2,2", [4, 1, 2]),
        (" 3 - 5 ", [3, 4, 5]),
    ],
)
def test_page_range(spec, expected):
    assert parse_page_range(spec, 5) == expected


@pytest.mark.parametrize("spec", ["0", "6", "3-2", "a", "1-", "1--2", "1,,2"])
def test_page_range_invalid(spec):
    with pytest.raises(AnalysisError) as exc:
        parse_page_range(spec, 5)
    assert exc.value.code == "invalid_page_range"


def test_page_range_empty_document():
    with pytest.raises(AnalysisError):
        parse_page_range(None, 0)


def test_extension_of():
    assert extension_of("Trabalho Final.DOCX") == ".docx"
    assert extension_of("") == ""


def test_unsupported_format_rejected(tmp_path):
    with pytest.raises(AnalysisError) as exc:
        DocumentAnalyzer().analyze(tmp_path / "x.exe", "x.exe", tmp_path)
    assert exc.value.code == "unsupported_format"


def test_missing_tools_reported(tmp_path):
    analyzer = DocumentAnalyzer(soffice="/nonexistent/soffice", gs="/nonexistent/gs")
    src = tmp_path / "a.txt"
    src.write_text("x")
    with pytest.raises(AnalysisError) as exc:
        analyzer.to_pdf(src, ".txt", tmp_path)
    assert exc.value.code == "conversion_failed"
    with pytest.raises(AnalysisError) as exc:
        analyzer.ink_coverage(src)
    assert exc.value.code == "analysis_failed"
    with pytest.raises(AnalysisError):
        analyzer.color_pixels(src, tmp_path)
