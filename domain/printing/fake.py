"""FakePrinterGateway: impressora simulada para desenvolvimento e testes.

O estado vive em memória por impressora (``key``) e pode ser configurado:

    FakePrinterGateway.configure("12", behaviour="partial", partial_faces=3)

Comportamentos: ``complete`` (imprime tudo), ``partial`` (imprime N faces e aborta),
``fail`` (aborta sem imprimir), ``offline`` (inalcançável), ``stuck`` (nunca termina).
"""

import io
import itertools
from typing import ClassVar

from pypdf import PdfReader

from .gateway import (
    JobOutcome,
    JobProgress,
    PrinterGateway,
    PrinterState,
    PrinterStatus,
    PrinterUnavailable,
    PrintRequest,
)

DEFAULTS = {
    "counter": 10_000,
    "state": PrinterState.READY,
    "reasons": [],
    "paper_out": False,
    "toner": {"black": 80, "cyan": 70, "magenta": 70, "yellow": 70},
    "behaviour": "complete",
    "partial_faces": 0,
    "counter_available": True,
}


class FakePrinterGateway(PrinterGateway):
    _printers: ClassVar[dict[str, dict]] = {}
    _ids = itertools.count(100)

    def __init__(self, key: str = "default"):
        self.key = str(key)
        self.state = self._printers.setdefault(self.key, self._fresh())

    @staticmethod
    def _fresh() -> dict:
        return {**DEFAULTS, "toner": dict(DEFAULTS["toner"]), "jobs": {}, "submitted": []}

    @classmethod
    def configure(cls, key, **values) -> dict:
        state = cls._printers.setdefault(str(key), cls._fresh())
        state.update(values)
        return state

    @classmethod
    def reset(cls) -> None:
        cls._printers.clear()

    def _check_online(self):
        if self.state["behaviour"] == "offline":
            raise PrinterUnavailable("Fake printer is offline")

    def get_status(self) -> PrinterStatus:
        self._check_online()
        state = self.state["state"]
        return PrinterStatus(
            state=state,
            can_print=self.state.get("accepting", True)
            and state in (PrinterState.READY, PrinterState.BUSY, PrinterState.WARNING),
            paper_out=self.state["paper_out"],
            reasons=list(self.state["reasons"]),
            toner=dict(self.state["toner"]),
        )

    def get_page_counter(self) -> int | None:
        if self.state["behaviour"] == "offline" or not self.state["counter_available"]:
            return None
        return self.state["counter"]

    def submit(self, request: PrintRequest) -> str:
        self._check_online()
        pages = len(PdfReader(io.BytesIO(request.pdf)).pages)
        faces = pages * request.copies
        job_id = str(next(self._ids))
        behaviour = self.state["behaviour"]
        if behaviour == "complete":
            printed, outcome = faces, JobOutcome.COMPLETED
        elif behaviour == "partial":
            printed, outcome = min(self.state["partial_faces"], faces), JobOutcome.ABORTED
        elif behaviour == "stuck":
            printed, outcome = 0, JobOutcome.PROCESSING
        else:
            printed, outcome = 0, JobOutcome.ABORTED
        self.state["counter"] += printed
        self.state["jobs"][job_id] = JobProgress(outcome=outcome, impressions=printed)
        self.state["submitted"].append(
            {
                "job_id": job_id,
                "job_name": request.job_name,
                "pages": pages,
                "copies": request.copies,
                "color": request.color,
                "duplex": request.duplex,
            }
        )
        return job_id

    def get_job_state(self, printer_job_id: str) -> JobProgress:
        self._check_online()
        return self.state["jobs"].get(printer_job_id, JobProgress(outcome=JobOutcome.UNKNOWN))
