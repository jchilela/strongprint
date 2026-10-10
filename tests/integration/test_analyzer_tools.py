"""DocumentAnalyzer com as ferramentas reais (LibreOffice + Ghostscript da imagem Docker)."""

import shutil

import pytest
from PIL import Image

from domain.analysis.analyzer import DocumentAnalyzer
from domain.analysis.types import AnalysisError
from tests.docs import encrypt_pdf, make_pdf, page

pytestmark = [
    pytest.mark.tools,
    pytest.mark.skipif(
        not (shutil.which("gs") and shutil.which("soffice")), reason="needs gs + soffice"
    ),
]


@pytest.fixture
def analyzer():
    return DocumentAnalyzer()


def test_pdf_pages_classified(tmp_path, analyzer):
    pdf = make_pdf(
        tmp_path / "doc.pdf",
        [
            page(),  # em branco
            page(box="black"),  # P&B, ~25% da página
            page(box="red", box_area=(50, 50, 150, 150)),  # cor, pouca cobertura
            page(background="blue"),  # cor, página inteira
            page(box=(90, 90, 90)),  # cinzento → P&B (não "preto rico")
        ],
    )
    out, analysis = analyzer.analyze(pdf, "doc.pdf", tmp_path)
    assert out == pdf
    assert analysis.page_count == 5
    blank, black, small_red, blue, gray = analysis.pages
    assert not gray.is_color
    assert (blank.is_color, blank.coverage_pct) == (False, 0.0)
    assert not black.is_color and 20 < black.coverage_pct < 30
    assert small_red.is_color and small_red.coverage_pct < 10
    assert blue.is_color and blue.coverage_pct > 30
    assert analysis.color_pages == 2


def test_image_converted_to_pdf(tmp_path, analyzer):
    src = tmp_path / "foto.png"
    Image.new("RGBA", (1200, 800), (0, 128, 0, 255)).save(src)  # paisagem, com alfa
    pdf, analysis = analyzer.analyze(src, "foto.png", tmp_path)
    assert pdf.suffix == ".pdf"
    assert analysis.page_count == 1 and analysis.pages[0].is_color
    assert analysis.source_format == "png"


def test_office_text_converted(tmp_path, analyzer):
    src = tmp_path / "upload.bin"
    src.write_text("Trabalho de grupo\nUniversidade\n" * 5, encoding="utf-8")
    pdf, analysis = analyzer.analyze(src, "notas.txt", tmp_path)
    assert pdf.name == "input.pdf"
    assert analysis.page_count == 1
    assert not analysis.pages[0].is_color
    assert 0 < analysis.pages[0].coverage_pct < 10


def test_corrupt_files_rejected(tmp_path, analyzer):
    bad_pdf = tmp_path / "bad.pdf"
    bad_pdf.write_bytes(b"%PDF-1.4 nothing here")
    with pytest.raises(AnalysisError) as exc:
        analyzer.analyze(bad_pdf, "bad.pdf", tmp_path)
    assert exc.value.code in {"corrupt_file", "empty_document"}

    bad_img = tmp_path / "bad.jpg"
    bad_img.write_bytes(b"not an image")
    with pytest.raises(AnalysisError) as exc:
        analyzer.analyze(bad_img, "bad.jpg", tmp_path)
    assert exc.value.code == "corrupt_file"


def test_encrypted_pdf_rejected(tmp_path, analyzer):
    plain = make_pdf(tmp_path / "plain.pdf", [page()])
    locked = encrypt_pdf(plain, tmp_path / "locked.pdf")
    with pytest.raises(AnalysisError) as exc:
        analyzer.analyze(locked, "locked.pdf", tmp_path)
    assert exc.value.code == "encrypted"


def test_page_limit(tmp_path):
    pdf = make_pdf(tmp_path / "long.pdf", [page(), page(), page()])
    with pytest.raises(AnalysisError) as exc:
        DocumentAnalyzer(max_pages=2).analyze(pdf, "long.pdf", tmp_path)
    assert exc.value.code == "too_many_pages"


def test_same_workdir_can_be_reused(tmp_path, analyzer):
    two = make_pdf(tmp_path / "two.pdf", [page(), page()])
    one = make_pdf(tmp_path / "one.pdf", [page()])
    assert analyzer.analyze(two, "two.pdf", tmp_path)[1].page_count == 2
    assert analyzer.analyze(one, "one.pdf", tmp_path)[1].page_count == 1
