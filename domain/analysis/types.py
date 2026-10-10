from dataclasses import asdict, dataclass, field


class AnalysisError(Exception):
    """Documento que não pode ser impresso (formato, corrompido, protegido...).

    ``code`` é estável e serve para traduzir a mensagem no ecrã do kiosk.
    """

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class PageAnalysis:
    """Medidas de uma página.

    - ``coverage_pct``: área da página com tinta (canal CMYK mais alto do inkcov).
    - ``color_pixels_pct``: % de píxeis com cor real (não cinzentos), medida numa
      renderização RGB; decide se a página é a cores. Não se usa o C/M/Y do
      inkcov para isso porque o preto RGB é convertido em "preto rico" (C+M+Y+K).
    """

    number: int
    cyan: float
    magenta: float
    yellow: float
    black: float
    color_pixels_pct: float
    is_color: bool
    coverage_pct: float

    @classmethod
    def measured(
        cls,
        number: int,
        cmyk: tuple[float, float, float, float],
        color_pixels_pct: float,
        color_threshold_pct: float,
    ) -> "PageAnalysis":
        c, m, y, k = cmyk
        return cls(
            number=number,
            cyan=round(c, 5),
            magenta=round(m, 5),
            yellow=round(y, 5),
            black=round(k, 5),
            color_pixels_pct=round(color_pixels_pct, 3),
            is_color=color_pixels_pct >= color_threshold_pct,
            coverage_pct=round(min(max(c, m, y, k) * 100, 100.0), 2),
        )


@dataclass(frozen=True)
class DocumentAnalysis:
    pages: list[PageAnalysis] = field(default_factory=list)
    source_format: str = "pdf"

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def color_pages(self) -> int:
        return sum(1 for p in self.pages if p.is_color)

    def to_dict(self) -> dict:
        return {
            "source_format": self.source_format,
            "page_count": self.page_count,
            "color_pages": self.color_pages,
            "pages": [asdict(p) for p in self.pages],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DocumentAnalysis":
        return cls(
            pages=[PageAnalysis(**p) for p in data.get("pages", [])],
            source_format=data.get("source_format", "pdf"),
        )
