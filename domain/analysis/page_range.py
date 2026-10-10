import re

from .types import AnalysisError

_PART = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+)\s*)?$")


def parse_page_range(spec: str | None, page_count: int) -> list[int]:
    """Converte "1-3, 5, 8-10" na lista de páginas (1-based), por ordem e sem repetições.

    ``None`` ou vazio = todas as páginas.
    """
    if page_count < 1:
        raise AnalysisError("empty_document", "Document has no pages")
    if spec is None or not str(spec).strip():
        return list(range(1, page_count + 1))

    pages: list[int] = []
    seen: set[int] = set()
    for part in str(spec).split(","):
        match = _PART.match(part)
        if not match:
            raise AnalysisError("invalid_page_range", f"Invalid page range: {spec!r}")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end < start or end > page_count:
            raise AnalysisError(
                "invalid_page_range", f"Page range {part.strip()} outside 1-{page_count}"
            )
        for page in range(start, end + 1):
            if page not in seen:
                seen.add(page)
                pages.append(page)
    return pages
