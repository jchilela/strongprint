"""DocumentAnalyzer: converte para PDF, conta páginas e mede a cobertura de tinta.

Ferramentas externas (presentes na imagem Docker):
- LibreOffice headless (``soffice``) para Office/ODF/texto;
- Ghostscript (``gs -sDEVICE=inkcov``) para a cobertura CMYK por página.
"""

import re
import shutil
import subprocess
import uuid
from pathlib import Path

from PIL import Image, ImageChops, UnidentifiedImageError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .types import AnalysisError, DocumentAnalysis, PageAnalysis

OFFICE_EXTENSIONS = {
    ".doc",
    ".docx",
    ".odt",
    ".rtf",
    ".txt",
    ".xls",
    ".xlsx",
    ".ods",
    ".csv",
    ".ppt",
    ".pptx",
    ".odp",
}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
SUPPORTED_EXTENSIONS = {".pdf"} | OFFICE_EXTENSIONS | IMAGE_EXTENSIONS

A4_INCHES = (8.27, 11.69)
_INKCOV_LINE = re.compile(
    r"^\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+CMYK\s+OK", re.MULTILINE
)


def extension_of(filename: str) -> str:
    return Path(filename or "").suffix.lower()


def parse_inkcov(output: str) -> list[tuple[float, float, float, float]]:
    """Extrai (C, M, Y, K) por página da saída do ``gs -sDEVICE=inkcov``."""
    return [tuple(float(v) for v in m.groups()) for m in _INKCOV_LINE.finditer(output)]


class DocumentAnalyzer:
    # Píxel "com cor" quando max(R,G,B) − min(R,G,B) passa este valor (0–255);
    # abaixo disso são cinzentos, anti-aliasing ou ruído de digitalização.
    CHROMA_THRESHOLD = 40
    RENDER_DPI = 30

    def __init__(
        self,
        *,
        color_threshold_pct: float = 0.1,
        max_pages: int = 300,
        soffice: str = "soffice",
        gs: str = "gs",
        timeout: int = 180,
    ):
        self.color_threshold_pct = color_threshold_pct
        self.max_pages = max_pages
        self.soffice = soffice
        self.gs = gs
        self.timeout = timeout

    def analyze(self, source: Path, filename: str, workdir: Path) -> tuple[Path, DocumentAnalysis]:
        """Devolve (pdf, análise). ``workdir`` é uma pasta temporária do chamador."""
        ext = extension_of(filename)
        if ext not in SUPPORTED_EXTENSIONS:
            raise AnalysisError("unsupported_format", f"Unsupported file type: {ext or '?'}")
        pdf = self.to_pdf(Path(source), ext, Path(workdir))
        page_count = self.count_pages(pdf)
        if page_count > self.max_pages:
            raise AnalysisError("too_many_pages", f"Document has {page_count} pages")
        ink = self.ink_coverage(pdf)
        color = self.color_pixels(pdf, workdir)
        if not len(ink) == len(color) == page_count:
            raise AnalysisError(
                "analysis_failed", f"Measured {len(ink)}/{len(color)} of {page_count} pages"
            )
        pages = [
            PageAnalysis.measured(i, cmyk, color_pct, self.color_threshold_pct)
            for i, (cmyk, color_pct) in enumerate(zip(ink, color, strict=True), start=1)
        ]
        return pdf, DocumentAnalysis(pages=pages, source_format=ext.lstrip("."))

    # --- conversão -------------------------------------------------------

    def to_pdf(self, source: Path, ext: str, workdir: Path) -> Path:
        if ext == ".pdf":
            return source
        if ext in IMAGE_EXTENSIONS:
            return self._image_to_pdf(source, workdir)
        return self._office_to_pdf(source, ext, workdir)

    def _image_to_pdf(self, source: Path, workdir: Path) -> Path:
        target = workdir / "converted.pdf"
        try:
            with Image.open(source) as img:
                img = img.convert("RGB")
                width, height = img.size
                page_w, page_h = A4_INCHES if height >= width else A4_INCHES[::-1]
                dpi = max(width / page_w, height / page_h, 72)
                img.save(target, "PDF", resolution=dpi)
        except (UnidentifiedImageError, OSError) as exc:
            raise AnalysisError("corrupt_file", "Image could not be read") from exc
        return target

    def _office_to_pdf(self, source: Path, ext: str, workdir: Path) -> Path:
        # O LibreOffice usa o nome do ficheiro para decidir o filtro; garantimos a extensão.
        named = workdir / f"input{ext}"
        if source != named:
            shutil.copyfile(source, named)
        profile = workdir / f"lo-profile-{uuid.uuid4().hex}"
        cmd = [
            self.soffice,
            f"-env:UserInstallation=file://{profile}",
            "--headless",
            "--norestore",
            "--convert-to",
            "pdf",
            "--outdir",
            str(workdir),
            str(named),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise AnalysisError("conversion_timeout", "Conversion took too long") from exc
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            raise AnalysisError("conversion_failed", "Document could not be converted") from exc
        finally:
            shutil.rmtree(profile, ignore_errors=True)
        target = workdir / "input.pdf"
        if not target.exists() or target.stat().st_size == 0:
            raise AnalysisError("conversion_failed", "Document could not be converted")
        return target

    # --- medição ---------------------------------------------------------

    def count_pages(self, pdf: Path) -> int:
        try:
            reader = PdfReader(str(pdf))
            if reader.is_encrypted and not reader.decrypt(""):
                raise AnalysisError("encrypted", "Document is password protected")
            count = len(reader.pages)
        except AnalysisError:
            raise
        except (PdfReadError, ValueError, KeyError, OSError) as exc:
            raise AnalysisError("corrupt_file", "Document could not be read") from exc
        if count < 1:
            raise AnalysisError("empty_document", "Document has no pages")
        return count

    def color_pixels(self, pdf: Path, workdir: Path) -> list[float]:
        """% de píxeis com cor em cada página (renderização RGB de baixa resolução)."""
        outdir = workdir / f"render-{uuid.uuid4().hex}"
        outdir.mkdir()
        self._gs(
            [
                "-sDEVICE=png16m",
                f"-r{self.RENDER_DPI}",
                "-dTextAlphaBits=1",
                "-dGraphicsAlphaBits=1",
                "-o",
                str(outdir / "p%05d.png"),
                str(pdf),
            ]
        )
        return [self.color_fraction(path) for path in sorted(outdir.glob("p*.png"))]

    @classmethod
    def color_fraction(cls, image_path: Path) -> float:
        with Image.open(image_path) as img:
            r, g, b = img.convert("RGB").split()
            high = ImageChops.lighter(ImageChops.lighter(r, g), b)
            low = ImageChops.darker(ImageChops.darker(r, g), b)
            chroma = ImageChops.subtract(high, low)
            colored = chroma.point(lambda v: 255 if v > cls.CHROMA_THRESHOLD else 0)
            count = colored.histogram()[255]
            return 100.0 * count / (img.width * img.height)

    def _gs(self, args: list[str]) -> str:
        cmd = [self.gs, "-q", "-dSAFER", "-dNOPAUSE", "-dBATCH", *args]
        try:
            result = subprocess.run(
                cmd, check=True, capture_output=True, text=True, timeout=self.timeout
            )
        except subprocess.TimeoutExpired as exc:
            raise AnalysisError("analysis_timeout", "Analysis took too long") from exc
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            raise AnalysisError("analysis_failed", "Document could not be measured") from exc
        return result.stdout

    def ink_coverage(self, pdf: Path) -> list[tuple[float, float, float, float]]:
        return parse_inkcov(self._gs(["-r72", "-sDEVICE=inkcov", "-o", "-", str(pdf)]))
