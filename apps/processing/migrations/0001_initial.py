import uuid

from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    initial = True
    dependencies = [("library", "0002_media")]
    operations = [migrations.CreateModel(
        name="ProcessingJob",
        fields=[
            ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ("kind", models.CharField(choices=[("inspect", "Validation et métadonnées"), ("render", "Miniature et aperçu")], max_length=8)),
            ("status", models.CharField(choices=[("pending", "En attente"), ("running", "En cours"), ("done", "Terminé"), ("error", "Erreur"), ("skipped", "Désactivé")], default="pending", max_length=8)),
            ("attempts", models.PositiveSmallIntegerField(default=0)),
            ("available_at", models.DateTimeField(default=django.utils.timezone.now)),
            ("claim_token", models.UUIDField(blank=True, editable=False, null=True)),
            ("lease_expires_at", models.DateTimeField(blank=True, null=True)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("started_at", models.DateTimeField(blank=True, null=True)),
            ("finished_at", models.DateTimeField(blank=True, null=True)),
            ("error_code", models.CharField(blank=True, max_length=40)),
            ("error_message", models.CharField(blank=True, max_length=240)),
            ("media", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="jobs", to="library.mediaitem")),
        ],
        options={
            "ordering": ("-created_at",), "verbose_name": "traitement", "verbose_name_plural": "traitements",
            "indexes": [models.Index(fields=["status", "available_at"], name="job_pending_available"), models.Index(fields=["status", "lease_expires_at"], name="job_running_lease")],
            "constraints": [models.UniqueConstraint(condition=models.Q(status__in=("pending", "running")), fields=("media", "kind"), name="one_active_job_per_media_kind")],
        },
    )]
