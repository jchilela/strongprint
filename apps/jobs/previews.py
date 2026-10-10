"""Pré-visualização das páginas de trabalhos e documentos.

As imagens ficam em ``previews/...`` (fora de ``jobs/{id}/``), por isso sobrevivem à
limpeza do ficheiro original (24 h) e mostram o histórico durante PREVIEW_RETENTION_DAYS.
São servidas por URLs assinados e com prazo, criados só para o dono.
"""

import time
from pathlib import Path

import structlog
from botocore.exceptions import ClientError
from django.conf import settings
from django.core import signing
from django.http import Http404, HttpResponse
from django.urls import reverse

from common import storage
from domain.analysis.preview import render_previews

from .models import Job

log = structlog.get_logger(__name__)
SALT = "strongprint.preview"
# O prazo é arredondado à hora: o mesmo URL repete-se durante essa hora e o browser
# aproveita a cache (o ecrã redesenha as opções a cada toque).
URL_HOURS = 6


def prefix_for(obj) -> str:
    kind = "jobs" if isinstance(obj, Job) else "documents"
    return f"previews/{kind}/{obj.pk}/"


def store_previews(obj, pdf: Path, workdir: Path) -> int:
    """Gera e guarda as imagens; devolve quantas. Uma falha aqui nunca impede a impressão."""
    try:
        pages = render_previews(pdf, workdir / "previews", max_pages=settings.PREVIEW_MAX_PAGES)
        prefix = prefix_for(obj)
        for i, page in enumerate(pages, start=1):
            storage.upload_file(page, f"{prefix}p-{i:03d}.jpg", "image/jpeg")
        return len(pages)
    except Exception:
        log.exception("preview.failed", obj=str(obj.pk))
        return 0


def preview_count(obj) -> int:
    if isinstance(obj, Job) and obj.preview_purged_at:
        return 0
    return int((obj.analysis or {}).get("preview_pages") or 0)


def sign_key(key: str) -> str:
    expires = (int(time.time()) // 3600 + URL_HOURS) * 3600
    return signing.Signer(salt=SALT).sign_object({"k": key, "e": expires})


def preview_urls(obj) -> list[str]:
    prefix = prefix_for(obj)
    return [
        reverse("preview-image", args=[sign_key(f"{prefix}p-{i:03d}.jpg")])
        for i in range(1, preview_count(obj) + 1)
    ]


def delete_previews(obj) -> int:
    return storage.delete_prefix(prefix_for(obj))


def preview_image(request, token: str):
    try:
        data = signing.Signer(salt=SALT).unsign_object(token)
    except signing.BadSignature as exc:
        raise Http404 from exc
    key = data.get("k") if isinstance(data, dict) else None
    if not isinstance(key, str) or not key.startswith("previews/") or data.get("e", 0) < time.time():
        raise Http404
    try:
        obj = storage.s3_client().get_object(Bucket=settings.S3_BUCKET, Key=key)
    except ClientError as exc:
        raise Http404 from exc
    response = HttpResponse(obj["Body"].read(), content_type="image/jpeg")
    response["Cache-Control"] = "private, max-age=3600"
    return response


