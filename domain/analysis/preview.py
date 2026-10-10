"""Imagens das páginas (JPEG pequenos) para o cliente ver o que vai imprimir / imprimiu."""

import subprocess
from pathlib import Path


def render_previews(pdf: Path, outdir: Path, *, max_pages: int = 30, dpi: int = 50) -> list[Path]:
    """Uma JPEG por página (até ``max_pages``), por ordem. A4 a 50 dpi ≈ 413×585 px."""
    outdir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "gs",
            "-q",
            "-dSAFER",
            "-dNOPAUSE",
            "-dBATCH",
            "-sDEVICE=jpeg",
            "-dJPEGQ=70",
            f"-r{dpi}",
            "-dTextAlphaBits=4",
            "-dGraphicsAlphaBits=4",
            "-dFirstPage=1",
            f"-dLastPage={max_pages}",
            "-o",
            str(outdir / "p-%03d.jpg"),
            str(pdf),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    return sorted(outdir.glob("p-*.jpg"))
