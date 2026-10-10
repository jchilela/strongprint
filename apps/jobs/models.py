import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.devices.models import Printer
from apps.partners.models import Kiosk
from domain.jobs.state_machine import JobState


def default_options() -> dict:
    return {"color": False, "duplex": False, "copies": 1, "page_range": None}


STATE_LABELS = {
    JobState.CREATED: _("Created"),
    JobState.FILE_RECEIVED: _("File received"),
    JobState.ANALYZED: _("Ready to confirm"),
    JobState.RESERVED: _("Reserved"),
    JobState.QUEUED: _("Queued"),
    JobState.PRINTING: _("Printing"),
    JobState.COMPLETED: _("Printed"),
    JobState.PARTIAL: _("Partially printed"),
    JobState.FAILED: _("Failed"),
    JobState.REJECTED: _("File rejected"),
    JobState.CANCELLED: _("Cancelled"),
    JobState.EXPIRED: _("Expired"),
}


class Job(models.Model):
    State = models.TextChoices("State", [(s.name, (s.value, STATE_LABELS[s])) for s in JobState])

    class Source(models.TextChoices):
        USB = "USB", _("USB pen drive")
        UPLOAD = "UPLOAD", _("Web upload")
        WHATSAPP = "WHATSAPP", _("WhatsApp")
        COPY = "COPY", _("Copy (scanned at the kiosk)")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kiosk = models.ForeignKey(Kiosk, on_delete=models.PROTECT, related_name="jobs")
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="print_jobs"
    )
    state = models.CharField(
        _("state"), max_length=20, choices=State.choices, default=JobState.CREATED
    )
    source = models.CharField(
        _("source"), max_length=10, choices=Source.choices, default=Source.UPLOAD
    )

    original_filename = models.CharField(_("original filename"), max_length=255, blank=True)
    file_key = models.CharField(_("storage key"), max_length=500, blank=True)
    file_size = models.PositiveBigIntegerField(_("file size"), null=True, blank=True)
    mime_type = models.CharField(_("MIME type"), max_length=120, blank=True)

    pages = models.PositiveIntegerField(_("pages"), null=True, blank=True)
    analysis = models.JSONField(_("analysis"), default=dict, blank=True)
    # Cópias: estado da digitalização {status, batches, pages, error, duplex_original}.
    scan = models.JSONField(_("scan"), default=dict, blank=True)
    options = models.JSONField(_("options"), default=default_options)
    price_breakdown = models.JSONField(_("price breakdown"), default=dict, blank=True)
    total_kz = models.PositiveIntegerField(_("total (Kz)"), null=True, blank=True)

    printer = models.ForeignKey(
        Printer, null=True, blank=True, on_delete=models.SET_NULL, related_name="jobs"
    )
    printer_job_id = models.CharField(_("printer job ID"), max_length=60, blank=True)
    counter_before = models.PositiveBigIntegerField(_("counter before"), null=True, blank=True)
    counter_after = models.PositiveBigIntegerField(_("counter after"), null=True, blank=True)
    pages_printed = models.PositiveIntegerField(_("faces printed"), null=True, blank=True)
    failure_reason = models.CharField(_("failure reason"), max_length=255, blank=True)

    expires_at = models.DateTimeField(_("expires at"), null=True, blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)
    finished_at = models.DateTimeField(_("finished at"), null=True, blank=True)
    file_purged_at = models.DateTimeField(_("file purged at"), null=True, blank=True)
    preview_purged_at = models.DateTimeField(
        _("preview purged at"), null=True, blank=True
    )

    class Meta:
        verbose_name = _("print job")
        verbose_name_plural = _("print jobs")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["state", "expires_at"], name="job_state_expiry_idx"),
            models.Index(fields=["finished_at", "file_purged_at"], name="job_purge_idx"),
        ]

    def __str__(self):
        return f"Job {str(self.id)[:8]} [{self.state}]"


class JobEvent(models.Model):
    """Auditoria: cada transição de estado fica registada aqui."""

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="events")
    from_state = models.CharField(_("from"), max_length=20, blank=True)
    to_state = models.CharField(_("to"), max_length=20)
    actor = models.CharField(
        _("actor"), max_length=100, help_text=_("e.g. system, user:<id>, task:print")
    )
    data = models.JSONField(_("data"), default=dict, blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        verbose_name = _("job event")
        verbose_name_plural = _("job events")
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.from_state or '∅'} → {self.to_state}"


class Document(models.Model):
    """Ficheiro guardado pelo cliente ("Meus ficheiros") para imprimir em qualquer kiosk."""

    class State(models.TextChoices):
        PENDING = "PENDING", _("Analysing")
        READY = "READY", _("Ready")
        REJECTED = "REJECTED", _("Rejected")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="documents"
    )
    source = models.CharField(
        _("source"), max_length=10, choices=Job.Source.choices, default=Job.Source.UPLOAD
    )
    original_filename = models.CharField(_("filename"), max_length=255)
    file_key = models.CharField(_("storage key"), max_length=500)
    file_size = models.PositiveBigIntegerField(_("file size"), default=0)
    mime_type = models.CharField(_("MIME type"), max_length=120, blank=True)
    state = models.CharField(
        _("state"), max_length=10, choices=State.choices, default=State.PENDING
    )
    pages = models.PositiveIntegerField(_("pages"), null=True, blank=True)
    color_pages = models.PositiveIntegerField(_("colour pages"), null=True, blank=True)
    analysis = models.JSONField(_("analysis"), default=dict, blank=True)
    failure_reason = models.CharField(_("failure reason"), max_length=60, blank=True)
    expires_at = models.DateTimeField(_("expires at"))
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        verbose_name = _("document")
        verbose_name_plural = _("documents")
        ordering = ["-created_at"]

    def __str__(self):
        return self.original_filename

    @property
    def document_key(self) -> str:
        return f"documents/{self.id}/document.pdf"
