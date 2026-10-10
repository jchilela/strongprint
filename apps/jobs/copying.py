"""Cópias no kiosk: digitalizar (alimentador e/ou vidro) → juntar → analisar → imprimir.

Um trabalho de cópia é um Job com ``source=COPY``. Fica em CREATED enquanto o
cliente digitaliza (uma ou mais vezes); ``finish_scan`` junta as digitalizações
num PDF e o resto do caminho é igual ao de uma impressão (preço, carteira,
impressão verificada pelo contador).
"""

import io
import subprocess
import tempfile
import uuid
from pathlib import Path

import structlog
from django.conf import settings
from django.db import transaction
from pypdf import PdfReader, PdfWriter

from apps.devices.models import Printer
from common import storage
from domain.analysis.types import AnalysisError
from domain.jobs.state_machine import JobState
from domain.scanning.gateway import (
    EsclScannerGateway,
    FakeScannerGateway,
    ScannerGateway,
    ScanSource,
)

from . import services
from .models import Job

log = structlog.get_logger(__name__)


class CopyError(AnalysisError):
    """Erros do fluxo de cópia com código estável para o ecrã."""


def scanner_for(printer: Printer) -> ScannerGateway:
    kind = printer.gateway or settings.PRINTER_GATEWAY
    if kind == "ipp":
        return EsclScannerGateway(
            address=printer.vpn_ip,
            host_header=printer.lan_ip or None,
            scan_timeout=settings.SCAN_TIMEOUT_SECONDS,
        )
    return FakeScannerGateway(key=str(printer.pk))


def scan_key(job: Job, batch: int) -> str:
    return f"jobs/{job.id}/scan-{batch:03d}.pdf"


def start_copy(kiosk, customer, *, actor: str, duplex_original: bool) -> Job:
    if getattr(kiosk, "printer", None) is None:
        raise CopyError("no_printer", "This kiosk has no printer")
    job_id = uuid.uuid4()
    return services.create_job(
        id=job_id,
        kiosk=kiosk,
        customer=customer,
        actor=actor,
        source=Job.Source.COPY,
        original_filename="Cópia",
        file_key=f"jobs/{job_id}/original.pdf",
        mime_type="application/pdf",
        options={"color": False, "duplex": duplex_original, "copies": 1, "page_range": None},
        scan={
            "status": "idle",
            "batches": 0,
            "pages": 0,
            "error": "",
            "duplex_original": duplex_original,
        },
    )


def request_scan(job: Job, source: str | None) -> Job:
    """Marca a digitalização como em curso e lança-a (uma de cada vez por trabalho)."""
    from . import tasks

    with transaction.atomic():
        job = Job.objects.select_for_update().get(pk=job.pk)
        if job.source != Job.Source.COPY or job.state != JobState.CREATED:
            raise CopyError("not_a_copy_in_progress", "This job is not a copy being scanned")
        if job.scan.get("status") == "scanning":
            raise CopyError("scan_in_progress", "Already scanning")
        job.scan = {**job.scan, "status": "scanning", "error": ""}
        job.expires_at = services.session_deadline()
        job.save(update_fields=["scan", "expires_at", "updated_at"])
        transaction.on_commit(lambda: tasks.scan_batch.delay(str(job.pk), source or ""))
    return job


def compress(pdf: bytes, dpi: int) -> bytes:
    """Reduz o PDF digitalizado (dezenas de MB a 300 dpi) para tamanho de impressão."""
    with tempfile.TemporaryDirectory(prefix="sp-scan-") as tmp:
        src, out = Path(tmp) / "in.pdf", Path(tmp) / "out.pdf"
        src.write_bytes(pdf)
        try:
            subprocess.run(
                [
                    "gs",
                    "-q",
                    "-dSAFER",
                    "-dNOPAUSE",
                    "-dBATCH",
                    "-sDEVICE=pdfwrite",
                    "-dPDFSETTINGS=/printer",
                    "-dDownsampleColorImages=true",
                    f"-dColorImageResolution={dpi}",
                    "-dDownsampleGrayImages=true",
                    f"-dGrayImageResolution={dpi}",
                    "-o",
                    str(out),
                    str(src),
                ],
                check=True,
                capture_output=True,
                timeout=300,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            return pdf
        result = out.read_bytes()
        return result if 0 < len(result) < len(pdf) else pdf


def run_scan(job_id: str, source: str) -> Job:
    """Digitaliza um lote (corre na tarefa Celery)."""
    job = Job.objects.select_related("kiosk__printer").get(pk=job_id)
    printer = job.kiosk.printer
    scanner = scanner_for(printer)
    from domain.scanning.gateway import ScannerUnavailable

    try:
        status = scanner.status()
        chosen = (
            ScanSource(source)
            if source
            else (ScanSource.ADF if status.adf_loaded else ScanSource.PLATEN)
        )
        duplex = bool(job.scan.get("duplex_original")) and chosen == ScanSource.ADF
        pdf = scanner.scan(source=chosen, duplex=duplex, color=True, dpi=settings.SCAN_DPI)
        pdf = compress(pdf, settings.SCAN_DPI)
        pages = len(PdfReader(io.BytesIO(pdf)).pages)
    except ScannerUnavailable as exc:
        Job.objects.filter(pk=job.pk).update(
            scan={**job.scan, "status": "error", "error": exc.code}
        )
        log.warning("copy.scan_failed", job_id=str(job.pk), code=exc.code)
        return Job.objects.get(pk=job.pk)

    batch = int(job.scan.get("batches", 0)) + 1
    storage.upload_fileobj(io.BytesIO(pdf), scan_key(job, batch), "application/pdf")
    scan = {
        **job.scan,
        "status": "ready",
        "batches": batch,
        "pages": int(job.scan.get("pages", 0)) + pages,
        "last_source": chosen.value,
        "error": "",
    }
    Job.objects.filter(pk=job.pk).update(scan=scan)
    log.info("copy.scanned", job_id=str(job.pk), source=chosen.value, pages=pages, batch=batch)
    return Job.objects.get(pk=job.pk)


def finish_scan(job: Job, *, actor: str) -> Job:
    """Junta as digitalizações num PDF e manda-o para análise (preço)."""
    from . import tasks

    job = Job.objects.get(pk=job.pk)
    if job.source != Job.Source.COPY or job.state != JobState.CREATED:
        raise CopyError("not_a_copy_in_progress", "This job is not a copy being scanned")
    if job.scan.get("status") == "scanning":
        raise CopyError("scan_in_progress", "Still scanning")
    batches = int(job.scan.get("batches", 0))
    if batches < 1:
        raise CopyError("nothing_scanned", "Nothing was scanned")

    writer = PdfWriter()
    client = storage.s3_client()
    for batch in range(1, batches + 1):
        data = io.BytesIO()
        client.download_fileobj(settings.S3_BUCKET, scan_key(job, batch), data)
        for page in PdfReader(io.BytesIO(data.getvalue())).pages:
            writer.add_page(page)
    merged = io.BytesIO()
    writer.write(merged)
    size = merged.tell()
    merged.seek(0)
    storage.upload_fileobj(merged, job.file_key, "application/pdf")
    pages = int(job.scan.get("pages", 0))
    job = services.transition(
        job.pk,
        JobState.FILE_RECEIVED,
        actor=actor,
        data={"scanned_pages": pages, "batches": batches},
        file_size=size,
        original_filename=f"Cópia ({pages} pág.)",
    )
    transaction.on_commit(lambda: tasks.analyze_job.delay(str(job.pk)))
    return job
