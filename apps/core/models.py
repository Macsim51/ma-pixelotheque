"""Typed instance settings with bounded values suitable for a small server."""

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class AppSetting(models.Model):
    """A singleton, editable only in the superuser administration.

    Safety ceilings are enforced independently by the ingestion/worker code.
    Storing these values in one row keeps configuration auditable and avoids an
    untyped key/value system with accidental string/boolean conversions.
    """

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    auto_generate = models.BooleanField("générer les miniatures après l’upload", default=True)
    allow_family_edit = models.BooleanField("autoriser chacun à modifier ses photos", default=True)
    allow_family_sharing = models.BooleanField("autoriser le partage des albums par leur créateur", default=True)
    thumbnail_size = models.PositiveIntegerField("taille des miniatures (pixels)", default=480, validators=[MinValueValidator(120), MaxValueValidator(1024)])
    preview_size = models.PositiveIntegerField("taille des previews (pixels)", default=2048, validators=[MinValueValidator(480), MaxValueValidator(4096)])
    preview_quality = models.PositiveSmallIntegerField("qualité des previews WebP", default=82, validators=[MinValueValidator(40), MaxValueValidator(95)])
    max_upload_mb = models.PositiveSmallIntegerField("poids maximal par photo (Mio)", default=30, validators=[MinValueValidator(1), MaxValueValidator(100)])
    max_image_pixels = models.PositiveIntegerField("nombre maximal de pixels", default=40_000_000, validators=[MinValueValidator(1_000_000), MaxValueValidator(80_000_000)])
    worker_paused = models.BooleanField("mettre les traitements en pause", default=False)

    class Meta:
        verbose_name = "réglages de l’instance"
        verbose_name_plural = "réglages de l’instance"
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name="core_settings_singleton")]

    @classmethod
    def load(cls):
        """Load current settings without a stale process-local permissions cache."""
        instance, _ = cls.objects.get_or_create(pk=1)
        return instance

    def __str__(self):
        return "Réglages de Ma Pixelothèque"
