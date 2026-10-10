"""Gera as imagens das páginas para documentos e trabalhos que ainda têm o PDF guardado."""

import tempfile
from pathlib import Path

from django.core.management.base import BaseCommand

from apps.jobs import previews
from apps.jobs.models import Document, Job
from apps.jobs.tasks import document_key
from common import storage


class Command(BaseCommand):
    help = "Build page previews for stored documents and jobs that do not have them yet."

    def handle(self, *args, **options):
        done = 0
        items = [(d, d.document_key) for d in Document.objects.filter(state=Document.State.READY)]
        items += [(j, document_key(j)) for j in Job.objects.filter(file_purged_at__isnull=True)]
        for obj, key in items:
            if previews.preview_count(obj) or not storage.object_size(key):
                continue
            with tempfile.TemporaryDirectory() as tmp:
                pdf = Path(tmp) / "document.pdf"
                storage.download_file(key, pdf)
                count = previews.store_previews(obj, pdf, Path(tmp))
            type(obj).objects.filter(pk=obj.pk).update(
                analysis={**(obj.analysis or {}), "preview_pages": count}
            )
            done += 1
        self.stdout.write(f"previews built for {done} item(s)")
