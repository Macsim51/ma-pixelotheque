import uuid

from django.conf import settings
from django.db import migrations, models
from django.db.models import Q
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [("library", "0001_initial"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name="Tag",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, verbose_name="nom")),
                ("normalized", models.CharField(max_length=100, unique=True)),
            ],
            options={"ordering": ("normalized",)},
        ),
        migrations.CreateModel(
            name="MediaItem",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("media_type", models.CharField(choices=[("photo", "Photo")], default="photo", max_length=10)),
                ("title", models.CharField(blank=True, max_length=200, verbose_name="titre")),
                ("description", models.TextField(blank=True, max_length=5000, verbose_name="description")),
                ("original_name", models.CharField(max_length=255)),
                ("original_key", models.CharField(blank=True, max_length=500)),
                ("thumbnail_key", models.CharField(blank=True, max_length=500)),
                ("preview_key", models.CharField(blank=True, max_length=500)),
                ("size_bytes", models.PositiveBigIntegerField(default=0)),
                ("sha256", models.CharField(blank=True, max_length=64)),
                ("status", models.CharField(choices=[("pending", "En traitement"), ("ready", "Disponible"), ("error", "Erreur de traitement")], db_index=True, default="pending", max_length=10)),
                ("derivative_status", models.CharField(choices=[("pending", "En attente"), ("ready", "Disponibles"), ("skipped", "Génération désactivée"), ("error", "Erreur de génération")], default="pending", max_length=10)),
                ("width", models.PositiveIntegerField(blank=True, null=True)),
                ("height", models.PositiveIntegerField(blank=True, null=True)),
                ("captured_at", models.DateTimeField(blank=True, null=True, verbose_name="date EXIF")),
                ("taken_at_override", models.DateTimeField(blank=True, null=True, verbose_name="date corrigée")),
                ("sort_date", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("uploaded_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("camera", models.CharField(blank=True, db_index=True, max_length=200)),
                ("latitude", models.FloatField(blank=True, null=True)),
                ("longitude", models.FloatField(blank=True, null=True)),
                ("exif", models.JSONField(blank=True, default=dict)),
                ("uploader", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="uploaded_media", to=settings.AUTH_USER_MODEL)),
                ("tags", models.ManyToManyField(blank=True, related_name="media_items", to="library.tag")),
            ],
            options={
                "ordering": ("-sort_date", "-id"),
                "indexes": [models.Index(fields=["sort_date", "id"], name="media_sort_cursor")],
                "constraints": [
                    models.CheckConstraint(condition=Q(media_type="photo"), name="media_photo_only"),
                    models.CheckConstraint(condition=(Q(latitude__isnull=True, longitude__isnull=True) | Q(latitude__gte=-90, latitude__lte=90, longitude__gte=-180, longitude__lte=180, latitude__isnull=False, longitude__isnull=False)), name="media_valid_coordinates"),
                ],
            },
        ),
        migrations.CreateModel(
            name="AlbumMedia",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("added_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("album", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="media_memberships", to="library.album")),
                ("media", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="album_memberships", to="library.mediaitem")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("album", "media"), name="unique_album_media")]},
        ),
        migrations.AddField(
            model_name="mediaitem", name="albums",
            field=models.ManyToManyField(related_name="media_items", through="library.AlbumMedia", to="library.album"),
        ),
        migrations.CreateModel(
            name="Favorite",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("media", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="favorites", to="library.mediaitem")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="favorites", to=settings.AUTH_USER_MODEL)),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("user", "media"), name="unique_user_favorite")]},
        ),
    ]
