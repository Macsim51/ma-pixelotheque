import uuid

from django.db import models
from django.utils import timezone


class ProcessingJob(models.Model):
    class Kind(models.TextChoices):
        INSPECT = "inspect", "Validation et métadonnées"
        RENDER = "render", "Miniature et aperçu"

    class Status(models.TextChoices):
        PENDING = "pending", "En attente"
        RUNNING = "running", "En cours"
        DONE = "done", "Terminé"
        ERROR = "error", "Erreur"
        SKIPPED = "skipped", "Désactivé"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    media = models.ForeignKey("library.MediaItem", on_delete=models.CASCADE, related_name="jobs")
    kind = models.CharField(max_length=8, choices=Kind.choices)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now)
    claim_token = models.UUIDField(null=True, blank=True, editable=False)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=40, blank=True)
    error_message = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "traitement"
        verbose_name_plural = "traitements"
        indexes = [
            models.Index(fields=("status", "available_at"), name="job_pending_available"),
            models.Index(fields=("status", "lease_expires_at"), name="job_running_lease"),
        ]
        constraints = [models.UniqueConstraint(
            fields=("media", "kind"),
            condition=models.Q(status__in=("pending", "running")),
            name="one_active_job_per_media_kind",
        )]

    def __str__(self):
        return f"{self.get_kind_display()} — {self.media_id}"
