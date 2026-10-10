"""IppPrinterGateway: HP Color LaserJet Pro MFP 3303sdw (IPP Everywhere + SNMP) pela VPN."""

import subprocess
import tempfile
from pathlib import Path

from .gateway import (
    JobOutcome,
    JobProgress,
    PrinterGateway,
    PrinterState,
    PrinterStatus,
    PrinterUnavailable,
    PrintRequest,
)
from .ipp import (
    CANCEL_JOB,
    GET_JOB_ATTRIBUTES,
    GET_PRINTER_ATTRIBUTES,
    INTEGER,
    KEYWORD,
    MIME_MEDIA_TYPE,
    NAME,
    PRINT_JOB,
    IppClient,
    IppError,
)
from .snmp import PAGE_COUNTER_OID, SnmpError, snmp_get_int

PRINTER_STATE = {3: PrinterState.READY, 4: PrinterState.BUSY, 5: PrinterState.ERROR}
JOB_STATE = {
    3: JobOutcome.PENDING,
    4: JobOutcome.PENDING,  # pending-held
    5: JobOutcome.PROCESSING,
    6: JobOutcome.PROCESSING,  # processing-stopped
    7: JobOutcome.CANCELED,
    8: JobOutcome.ABORTED,
    9: JobOutcome.COMPLETED,
}
PAPER_REASONS = ("media-empty", "media-needed", "media-jam")
PRINTER_ATTRS = [
    "printer-state",
    "printer-state-reasons",
    "printer-is-accepting-jobs",
    "marker-names",
    "marker-colors",
    "marker-levels",
    "document-format-supported",
]


def map_status(attrs: dict[str, list]) -> PrinterStatus:
    state = PRINTER_STATE.get((attrs.get("printer-state") or [5])[0], PrinterState.ERROR)
    reasons = [r for r in attrs.get("printer-state-reasons", []) if r != "none"]
    accepting = bool((attrs.get("printer-is-accepting-jobs") or [True])[0])
    errors = [r for r in reasons if r.endswith("-error")]
    paper_out = any(r.startswith(PAPER_REASONS) for r in reasons)
    if errors or not accepting:
        state = PrinterState.ERROR
    elif state == PrinterState.READY and any(r.endswith("-warning") for r in reasons):
        state = PrinterState.WARNING

    names = attrs.get("marker-names", [])
    colors = attrs.get("marker-colors", [])
    levels = attrs.get("marker-levels", [])
    toner = {}
    for i, level in enumerate(levels):
        label = names[i] if i < len(names) else (colors[i] if i < len(colors) else f"marker-{i}")
        toner[str(label)] = int(level)
    formats = attrs.get("document-format-supported", [])
    return PrinterStatus(
        state=state,
        can_print=not paper_out
        and state in (PrinterState.READY, PrinterState.BUSY, PrinterState.WARNING),
        paper_out=paper_out,
        reasons=reasons,
        toner=toner,
        accepts_pdf=not formats or "application/pdf" in formats,
    )


class IppPrinterGateway(PrinterGateway):
    def __init__(
        self,
        *,
        printer_uri: str,
        host: str,
        snmp_community: str = "public",
        timeout: float = 10.0,
        transport=None,
        snmp_get=snmp_get_int,
    ):
        self.client = IppClient(printer_uri, timeout=timeout, transport=transport)
        self.host = host
        self.snmp_community = snmp_community
        self.snmp_get = snmp_get
        self._accepts_pdf: bool | None = None

    def _call(self, *args, **kwargs):
        try:
            return self.client.call(*args, **kwargs)
        except IppError as exc:
            raise PrinterUnavailable(str(exc)) from exc

    def get_status(self) -> PrinterStatus:
        response = self._call(
            GET_PRINTER_ATTRIBUTES, [(KEYWORD, "requested-attributes", PRINTER_ATTRS)]
        )
        status = map_status(response.first("printer"))
        self._accepts_pdf = status.accepts_pdf
        return status

    def get_page_counter(self) -> int | None:
        try:
            return self.snmp_get(self.host, PAGE_COUNTER_OID, community=self.snmp_community)
        except SnmpError:
            return None

    def submit(self, request: PrintRequest) -> str:
        if self._accepts_pdf is None:
            self.get_status()
        document, fmt = request.pdf, "application/pdf"
        if not self._accepts_pdf:
            document, fmt = pdf_to_pwg_raster(request.pdf, request.color), "image/pwg-raster"
        job_attrs = [
            (INTEGER, "copies", request.copies),
            (KEYWORD, "sides", "two-sided-long-edge" if request.duplex else "one-sided"),
            (KEYWORD, "print-color-mode", "color" if request.color else "monochrome"),
            (KEYWORD, "multiple-document-handling", "separate-documents-collated-copies"),
        ]
        response = self._call(
            PRINT_JOB,
            [
                (NAME, "requesting-user-name", "strongprint"),
                (NAME, "job-name", request.job_name[:120]),
                (MIME_MEDIA_TYPE, "document-format", fmt),
            ],
            job_attrs,
            document,
        )
        job_id = response.get("job", "job-id")
        if job_id is None:
            raise PrinterUnavailable("Printer did not return a job-id")
        return str(job_id)

    def get_job_state(self, printer_job_id: str) -> JobProgress:
        response = self._call(
            GET_JOB_ATTRIBUTES,
            [
                (INTEGER, "job-id", int(printer_job_id)),
                (
                    KEYWORD,
                    "requested-attributes",
                    ["job-state", "job-state-reasons", "job-impressions-completed"],
                ),
            ],
        )
        state = response.get("job", "job-state")
        return JobProgress(
            outcome=JOB_STATE.get(state, JobOutcome.UNKNOWN),
            impressions=response.get("job", "job-impressions-completed"),
            reasons=[r for r in response.get_all("job", "job-state-reasons") if r != "none"],
        )

    def cancel(self, printer_job_id: str) -> None:
        self._call(CANCEL_JOB, [(INTEGER, "job-id", int(printer_job_id))])


def pdf_to_pwg_raster(pdf: bytes, color: bool, dpi: int = 300) -> bytes:
    """Alternativa para impressoras que não aceitam PDF diretamente."""
    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp) / "in.pdf", Path(tmp) / "out.pwg"
        src.write_bytes(pdf)
        subprocess.run(
            [
                "gs",
                "-q",
                "-dSAFER",
                "-dNOPAUSE",
                "-dBATCH",
                "-sDEVICE=pwgraster",
                f"-r{dpi}",
                f"-dcupsColorSpace={19 if color else 18}",  # sRGB / sGray
                "-o",
                str(out),
                str(src),
            ],
            check=True,
            capture_output=True,
            timeout=300,
        )
        return out.read_bytes()
