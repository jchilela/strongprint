"""Execução física de um trabalho: contador → enviar → acompanhar → contador → liquidar.

Garantia principal: um trabalho **nunca é enviado duas vezes**. Se o worker cair
a meio, a retoma só acompanha/verifica pelo contador — nunca reenvia.
"""

import io
import time

import structlog
from django.conf import settings
from django.utils import timezone
from pypdf import PdfReader, PdfWriter

from apps.devices.models import Printer
from common import storage
from domain.jobs.state_machine import JobState
from domain.printing.fake import FakePrinterGateway
from domain.printing.gateway import (
    JobOutcome,
    JobProgress,
    PrinterGateway,
    PrinterUnavailable,
    PrintRequest,
)
from domain.printing.ipp_gateway import IppPrinterGateway
from domain.printing.verification import decide

from . import services
from .models import Job

log = structlog.get_logger(__name__)


class PrinterNotReady(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def gateway_for(printer: Printer) -> PrinterGateway:
    kind = printer.gateway or settings.PRINTER_GATEWAY
    if kind == "ipp":
        return IppPrinterGateway(
            printer_uri=printer.effective_ipp_uri,
            host=printer.vpn_ip,
            snmp_community=printer.snmp_community,
            timeout=settings.PRINTER_TIMEOUT_SECONDS,
        )
    return FakePrinterGateway(key=str(printer.pk))


def ensure_ready(printer: Printer | None, gateway: PrinterGateway | None = None):
    if printer is None:
        raise PrinterNotReady("no_printer")
    gateway = gateway or gateway_for(printer)
    try:
        status = gateway.get_status()
    except PrinterUnavailable as exc:
        raise PrinterNotReady("printer_offline") from exc
    if status.paper_out:
        raise PrinterNotReady("paper_out")
    if not status.can_print:
        raise PrinterNotReady("printer_error")
    return status


def build_print_pdf(job: Job) -> bytes:
    """PDF só com as páginas pagas, pela ordem pedida (as cópias vão por IPP)."""
    data = io.BytesIO()
    storage.s3_client().download_fileobj(settings.S3_BUCKET, f"jobs/{job.id}/document.pdf", data)
    reader = PdfReader(io.BytesIO(data.getvalue()))
    writer = PdfWriter()
    for line in job.price_breakdown["pages"]:
        writer.add_page(reader.pages[line["page"] - 1])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def start(job: Job, gateway: PrinterGateway | None = None) -> Job:
    """QUEUED → PRINTING → resultado. Lança PrinterNotReady (o chamador volta a tentar)."""
    printer = getattr(job.kiosk, "printer", None)
    if printer is None:
        raise PrinterNotReady("no_printer")
    gateway = gateway or gateway_for(printer)
    ensure_ready(printer, gateway)

    before = gateway.get_page_counter()
    job = services.transition(
        job.pk,
        JobState.PRINTING,
        actor="task:print",
        data={"counter_before": before},
        printer=printer,
        counter_before=before,
    )
    options = job.options
    request = PrintRequest(
        pdf=build_print_pdf(job),
        job_name=f"SP-{str(job.id)[:8]}",
        color=bool(options.get("color")),
        duplex=bool(options.get("duplex")),
        copies=int(options.get("copies", 1)),
    )
    try:
        printer_job_id = gateway.submit(request)
    except PrinterUnavailable as exc:
        # Pode ter sido recebido mesmo assim: o contador decide, nunca se reenvia.
        log.warning("print.submit_failed", reason=str(exc))
        return verify(job, gateway, JobProgress(outcome=JobOutcome.ABORTED))
    Job.objects.filter(pk=job.pk).update(printer_job_id=printer_job_id, updated_at=timezone.now())
    job.printer_job_id = printer_job_id
    log.info("print.submitted", printer_job_id=printer_job_id, faces=job.price_breakdown["faces"])
    return track(job, gateway)


def track(job: Job, gateway: PrinterGateway) -> Job:
    deadline = time.monotonic() + settings.PRINT_JOB_TIMEOUT_SECONDS
    progress = JobProgress(outcome=JobOutcome.UNKNOWN)
    while True:
        try:
            progress = gateway.get_job_state(job.printer_job_id)
        except PrinterUnavailable as exc:
            log.warning("print.poll_failed", reason=str(exc))
        if progress.outcome.is_final:
            break
        if time.monotonic() > deadline:
            log.warning("print.timeout", last=progress.outcome.value)
            try:
                gateway.cancel(job.printer_job_id)
            except PrinterUnavailable:
                pass
            break
        time.sleep(settings.PRINT_POLL_SECONDS)
    return verify(job, gateway, progress)


def verify(job: Job, gateway: PrinterGateway, progress: JobProgress) -> Job:
    time.sleep(settings.PRINT_COUNTER_SETTLE_SECONDS)  # o contador atualiza depois da folha sair
    after = gateway.get_page_counter()
    verdict = decide(
        job.price_breakdown,
        counter_before=job.counter_before,
        counter_after=after,
        outcome=progress.outcome,
        impressions=progress.impressions,
    )
    if verdict.overflow:
        log.warning("print.counter_overflow", overflow=verdict.overflow)
    log.info("print.verdict", **verdict.to_dict(), printer_outcome=progress.outcome.value)
    reason = "" if verdict.state == JobState.COMPLETED else f"printer_{progress.outcome.value}"
    return services.finish(
        job.pk,
        verdict.state,
        actor="task:print",
        capture_kz=verdict.capture_kz,
        counter_after=after,
        pages_printed=verdict.faces_printed,
        failure_reason=reason,
    )


def resume(job: Job, gateway: PrinterGateway | None = None) -> Job:
    """Retoma um trabalho que ficou em PRINTING (worker reiniciado)."""
    gateway = gateway or gateway_for(job.printer or job.kiosk.printer)
    if job.printer_job_id:
        return track(job, gateway)
    # Caiu entre PRINTING e o envio: não se sabe se foi enviado — o contador decide.
    return verify(job, gateway, JobProgress(outcome=JobOutcome.UNKNOWN))
