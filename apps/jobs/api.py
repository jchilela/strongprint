"""API v1 — trabalhos de impressão (ecrã do kiosk / portal do cliente)."""

import uuid

import structlog
from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.partners.models import Kiosk
from apps.wallets.services import wallet_for
from common import storage
from common.api import error
from common.auth import issue_kiosk_session, session_kiosk_id
from domain.analysis.analyzer import SUPPORTED_EXTENSIONS, extension_of
from domain.jobs.state_machine import JobState

from . import copying, library, services, tasks
from .previews import preview_urls
from .models import Document, Job

log = structlog.get_logger(__name__)

CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}

PROGRESS = {
    JobState.CREATED: 0,
    JobState.FILE_RECEIVED: 15,
    JobState.ANALYZED: 30,
    JobState.RESERVED: 45,
    JobState.QUEUED: 55,
    JobState.PRINTING: 75,
}


def max_upload_bytes() -> int:
    return settings.UPLOAD_MAX_MB * 1024 * 1024


class JobSerializer(serializers.ModelSerializer):
    kiosk = serializers.CharField(source="kiosk.code")
    progress = serializers.SerializerMethodField()
    preview_urls = serializers.SerializerMethodField()

    class Meta:
        model = Job
        fields = [
            "id",
            "kiosk",
            "state",
            "source",
            "original_filename",
            "pages",
            "options",
            "price_breakdown",
            "total_kz",
            "progress",
            "pages_printed",
            "failure_reason",
            "scan",
            "expires_at",
            "created_at",
            "updated_at",
            "finished_at",
            "preview_urls",
        ]

    def get_progress(self, job) -> int:
        return PROGRESS.get(job.state, 100)

    def get_preview_urls(self, job) -> list[str]:
        return preview_urls(job)


class CreateJobSerializer(serializers.Serializer):
    filename = serializers.CharField(max_length=255, required=False)
    document_id = serializers.UUIDField(required=False)
    source = serializers.ChoiceField(choices=Job.Source.choices, default=Job.Source.UPLOAD)
    size = serializers.IntegerField(required=False, min_value=1)

    def validate_filename(self, value):
        if extension_of(value) not in SUPPORTED_EXTENSIONS:
            raise serializers.ValidationError("unsupported_format")
        return value

    def validate_size(self, value):
        if value > max_upload_bytes():
            raise serializers.ValidationError("file_too_large")
        return value

    def validate(self, attrs):
        if not attrs.get("filename") and not attrs.get("document_id"):
            raise serializers.ValidationError("filename_or_document_required")
        return attrs


class OptionsSerializer(serializers.Serializer):
    color = serializers.BooleanField(required=False)
    duplex = serializers.BooleanField(required=False)
    copies = serializers.IntegerField(required=False, min_value=1, max_value=50)
    page_range = serializers.CharField(required=False, allow_blank=True, allow_null=True)


def kiosk_for_request(request, code: str) -> Kiosk:
    kiosk = get_object_or_404(Kiosk.objects.select_related("site"), code=code, is_active=True)
    bound = session_kiosk_id(request)
    if bound is not None and bound != kiosk.pk:
        raise serializers.ValidationError({"error": "wrong_kiosk"})
    return kiosk


def job_for_request(request, job_id) -> Job:
    """O cliente só vê os seus trabalhos; uma sessão de kiosk só os desse kiosk."""
    qs = Job.objects.select_related("kiosk__site")
    if not request.user.is_staff:
        qs = qs.filter(customer=request.user)
    bound = session_kiosk_id(request)
    if bound is not None:
        qs = qs.filter(kiosk_id=bound)
    job = get_object_or_404(qs, pk=job_id)
    structlog.contextvars.bind_contextvars(job_id=str(job.id))
    return job


def job_payload(job: Job, request, **extra) -> dict:
    data = JobSerializer(job).data
    w = wallet_for(job.customer)
    data["wallet"] = {"available_kz": w.available_kz}
    if job.total_kz is not None:
        data["wallet"]["missing_kz"] = max(0, job.total_kz - w.available_kz)
    data.update(extra)
    return data


def check_printer_capabilities(job: Job, options: dict):
    printer = getattr(job.kiosk, "printer", None)
    if printer is None:
        return
    if options.get("color") and not printer.supports_color:
        raise serializers.ValidationError({"error": "color_not_available"})
    if options.get("duplex") and not printer.supports_duplex:
        raise serializers.ValidationError({"error": "duplex_not_available"})


def actor_for(request) -> str:
    kiosk_id = session_kiosk_id(request)
    return f"user:{request.user.pk}" + (f"@kiosk:{kiosk_id}" if kiosk_id else "")


def enqueue_analysis(job: Job):
    transaction.on_commit(lambda: tasks.analyze_job.delay(str(job.pk)))


# --- endpoints -------------------------------------------------------------


@api_view(["POST"])
def kiosk_session(request, code):
    """Liga o cliente autenticado a um kiosk e devolve o token de sessão do ecrã."""
    kiosk = get_object_or_404(Kiosk, code=code, is_active=True)
    return Response(
        {"kiosk": kiosk.code, **issue_kiosk_session(request.user, kiosk)},
        status=status.HTTP_201_CREATED,
    )


@api_view(["POST"])
def create_job(request, code):
    kiosk = kiosk_for_request(request, code)
    payload = CreateJobSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    if document_id := payload.validated_data.get("document_id"):
        # Ficheiro de "Meus ficheiros": já está guardado, vai direto para análise.
        doc = get_object_or_404(Document, pk=document_id, owner=request.user)
        job = library.job_from_document(doc, kiosk, actor=actor_for(request))
        return Response(job_payload(job, request), status=status.HTTP_201_CREATED)
    filename = payload.validated_data["filename"]
    ext = extension_of(filename)
    job_id = uuid.uuid4()
    job = services.create_job(
        id=job_id,
        kiosk=kiosk,
        customer=request.user,
        actor=actor_for(request),
        source=payload.validated_data["source"],
        original_filename=filename,
        file_key=f"jobs/{job_id}/original{ext}",
        mime_type=CONTENT_TYPES.get(ext, "application/octet-stream"),
    )
    structlog.contextvars.bind_contextvars(job_id=str(job.id))
    upload = {
        "method": "POST",
        "url": request.build_absolute_uri(f"/api/v1/jobs/{job.id}/file"),
        "field": "file",
        "max_bytes": max_upload_bytes(),
    }
    presigned = storage.presigned_put(job.file_key, job.mime_type)
    return Response(
        job_payload(job, request, upload=upload, presigned_put=presigned),
        status=status.HTTP_201_CREATED,
    )


@api_view(["POST"])
@parser_classes([MultiPartParser, FormParser])
def upload_file(request, job_id):
    """Envio direto do ficheiro (pen USB no Pi, portal, WhatsApp)."""
    job = job_for_request(request, job_id)
    upload = request.FILES.get("file")
    if upload is None:
        return error("file_missing", "Send the document in the 'file' field", 400)
    if upload.size > max_upload_bytes():
        return error("file_too_large", f"Max {settings.UPLOAD_MAX_MB} MB", 413)
    if job.state != JobState.CREATED:
        return error("invalid_state", f"Not allowed while the job is {job.state}", 409)
    filename = job.original_filename or upload.name
    if extension_of(upload.name) != extension_of(filename):
        return error("extension_mismatch", "File type differs from the job's filename", 400)
    storage.upload_fileobj(upload, job.file_key, job.mime_type)
    job = services.transition(
        job.pk,
        JobState.FILE_RECEIVED,
        actor=actor_for(request),
        data={"size": upload.size},
        file_size=upload.size,
    )
    enqueue_analysis(job)
    return Response(job_payload(job, request), status=status.HTTP_202_ACCEPTED)


@api_view(["POST"])
def mark_uploaded(request, job_id):
    """Confirmação após envio direto ao S3 (URL pré-assinado)."""
    job = job_for_request(request, job_id)
    size = storage.object_size(job.file_key)
    if size is None:
        return error("file_missing", "File not found in storage", 400)
    if size > max_upload_bytes():
        return error("file_too_large", f"Max {settings.UPLOAD_MAX_MB} MB", 413)
    job = services.transition(
        job.pk,
        JobState.FILE_RECEIVED,
        actor=actor_for(request),
        data={"size": size},
        file_size=size,
    )
    enqueue_analysis(job)
    return Response(job_payload(job, request), status=status.HTTP_202_ACCEPTED)


@api_view(["GET"])
def job_detail(request, job_id):
    job = job_for_request(request, job_id)
    return Response(job_payload(job, request))


@api_view(["PATCH"])
@parser_classes([JSONParser])
def job_options(request, job_id):
    job = job_for_request(request, job_id)
    payload = OptionsSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    check_printer_capabilities(job, payload.validated_data)
    job = services.update_options(job.pk, payload.validated_data, actor=actor_for(request))
    return Response(job_payload(job, request))


@api_view(["POST"])
def job_checkout(request, job_id):
    """Cativa o valor na carteira; sem saldo devolve 402 com o valor em falta."""
    job = job_for_request(request, job_id)
    job = services.checkout(job.pk, actor=actor_for(request))
    return Response(job_payload(job, request))


@api_view(["POST"])
def job_cancel(request, job_id):
    job = job_for_request(request, job_id)
    job = services.cancel(job.pk, actor=actor_for(request))
    return Response(job_payload(job, request))


# --- cópias ---------------------------------------------------------------------


class CopyStartSerializer(serializers.Serializer):
    duplex_original = serializers.BooleanField(default=False)
    source = serializers.ChoiceField(choices=["", "adf", "platen"], default="")


class ScanSerializer(serializers.Serializer):
    source = serializers.ChoiceField(choices=["", "adf", "platen"], default="")


@api_view(["POST"])
def copy_start(request, code):
    """Começa uma cópia e digitaliza logo o primeiro lote."""
    kiosk = kiosk_for_request(request, code)
    payload = CopyStartSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    job = copying.start_copy(
        kiosk,
        request.user,
        actor=actor_for(request),
        duplex_original=payload.validated_data["duplex_original"],
    )
    structlog.contextvars.bind_contextvars(job_id=str(job.id))
    job = copying.request_scan(job, payload.validated_data["source"])
    return Response(job_payload(job, request), status=status.HTTP_201_CREATED)


@api_view(["POST"])
def copy_scan(request, job_id):
    """Digitaliza mais folhas (vidro ou alimentador) para a mesma cópia."""
    job = job_for_request(request, job_id)
    payload = ScanSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    job = copying.request_scan(job, payload.validated_data["source"])
    return Response(job_payload(job, request), status=status.HTTP_202_ACCEPTED)


@api_view(["POST"])
def copy_finish(request, job_id):
    """Termina a digitalização: junta as folhas e calcula o preço."""
    job = job_for_request(request, job_id)
    job = copying.finish_scan(job, actor=actor_for(request))
    return Response(job_payload(job, request), status=status.HTTP_202_ACCEPTED)
