import uuid
import math
import unicodedata

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone


class AlbumQuerySet(models.QuerySet):
    def uploadable_to(self, user):
        """Albums that may receive contributions, without per-album queries."""
        if not user.is_authenticated or not user.is_active:
            return self.none()
        if user.is_superuser:
            return self
        if user.role != "family":
            return self.none()
        contribution = AlbumPermission.objects.filter(
            album_id=OuterRef("pk"), user_id=user.pk, can_upload=True
        )
        return self.alias(_can_contribute=Exists(contribution)).filter(
            Q(owner_id=user.pk)
            | Q(visibility=Album.Visibility.FAMILY, allow_family_uploads=True)
            | Q(visibility=Album.Visibility.RESTRICTED, _can_contribute=True)
        )

    @transaction.atomic
    def delete(self):
        selected = self.order_by().values("pk")
        alternatives = AlbumMedia.objects.filter(media_id=OuterRef("media_id")).exclude(album_id__in=selected)
        last_memberships = AlbumMedia.objects.filter(album_id__in=selected).alias(
            _has_alternative=Exists(alternatives)
        ).filter(_has_alternative=False)
        if last_memberships.exists():
            raise models.ProtectedError(
                "Déplacez les photos dans un autre album avant de supprimer leur dernier album.",
                list(self[:1]),
            )
        return super().delete()

    def visible_to(self, user):
        """Apply access rules in SQL, before counts, pagination or aggregation."""
        if not user.is_authenticated or not user.is_active:
            return self.none()
        if user.is_superuser:
            return self

        explicit_access = AlbumPermission.objects.filter(
            album_id=OuterRef("pk"), user_id=user.pk
        )
        allowed = Q(owner_id=user.pk) | Q(
            visibility__in=(Album.Visibility.FAMILY, Album.Visibility.RESTRICTED),
            _explicit_access=True,
        )
        if user.role == "family":
            allowed |= Q(visibility=Album.Visibility.FAMILY)
        return self.alias(_explicit_access=Exists(explicit_access)).filter(allowed)


class Album(models.Model):
    class Visibility(models.TextChoices):
        FAMILY = "family", "Familial"
        PRIVATE = "private", "Privé"
        RESTRICTED = "restricted", "Restreint"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="owned_albums",
        verbose_name="créateur",
    )
    title = models.CharField("titre", max_length=150)
    description = models.TextField("description", max_length=2000, blank=True)
    visibility = models.CharField(
        "visibilité",
        max_length=10,
        choices=Visibility.choices,
        default=Visibility.FAMILY,
        db_index=True,
    )
    allow_family_uploads = models.BooleanField(
        "autoriser les contributions familiales", default=True
    )
    created_at = models.DateTimeField("créé le", auto_now_add=True)
    updated_at = models.DateTimeField("modifié le", auto_now=True)

    objects = AlbumQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at", "-id")
        verbose_name = "album"
        verbose_name_plural = "albums"

    def __str__(self):
        return self.title

    def can_manage(self, user):
        from .permissions import can_manage_album

        return can_manage_album(user, self)

    def can_upload(self, user):
        from .permissions import can_upload_to_album

        return can_upload_to_album(user, self)

    def can_delete_safely(self):
        alternatives = AlbumMedia.objects.filter(media_id=OuterRef("media_id")).exclude(album_id=self.pk)
        return not self.media_memberships.alias(_has_alternative=Exists(alternatives)).filter(_has_alternative=False).exists()

    def delete(self, using=None, keep_parents=False):
        return type(self).objects.using(using or self._state.db).filter(pk=self.pk).delete()


class AlbumPermission(models.Model):
    album = models.ForeignKey(
        Album, on_delete=models.CASCADE, related_name="permissions"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="album_permissions",
        verbose_name="utilisateur",
    )
    can_upload = models.BooleanField("peut ajouter des photos", default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("album", "user"), name="unique_album_user_permission"
            )
        ]
        verbose_name = "accès à un album"
        verbose_name_plural = "accès aux albums"

    def __str__(self):
        return f"{self.user} — {self.album}"

    def clean(self):
        super().clean()
        errors = {}
        if self.album_id:
            if self.album.visibility == Album.Visibility.PRIVATE:
                errors["album"] = "Un album privé ne peut pas avoir de membres."
            if self.user_id and self.album.owner_id == self.user_id:
                errors["user"] = "Le créateur dispose déjà d’un accès à cet album."
        if self.user_id:
            if not self.user.is_active:
                errors["user"] = "Choisissez un compte actif."
            if self.can_upload and self.user.role != "family" and not self.user.is_superuser:
                errors["can_upload"] = "Un compte invité ne peut pas ajouter de photos."
        if errors:
            raise ValidationError(errors)


class Tag(models.Model):
    name = models.CharField("nom", max_length=100)
    normalized = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ("normalized",)

    @staticmethod
    def normalize(value):
        return " ".join(unicodedata.normalize("NFKC", value).casefold().split())

    def save(self, *args, **kwargs):
        self.name = " ".join(unicodedata.normalize("NFKC", self.name).split())
        self.normalized = self.normalize(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class MediaItemQuerySet(models.QuerySet):
    def visible_to(self, user):
        if not user.is_authenticated or not user.is_active:
            return self.none()
        memberships = AlbumMedia.objects.filter(
            media_id=OuterRef("pk"),
            album_id__in=Album.objects.visible_to(user).values("pk"),
        )
        result = self.alias(_album_access=Exists(memberships)).filter(_album_access=True)
        if user.is_superuser:
            return result
        return result.filter(Q(status=MediaItem.Status.READY) | Q(uploader_id=user.pk))

    def with_favorite(self, user):
        return self.annotate(
            is_favorite=Exists(Favorite.objects.filter(media_id=OuterRef("pk"), user_id=user.pk))
        )


class MediaItem(models.Model):
    class MediaType(models.TextChoices):
        PHOTO = "photo", "Photo"

    class Status(models.TextChoices):
        PENDING = "pending", "En traitement"
        READY = "ready", "Disponible"
        ERROR = "error", "Erreur de traitement"

    class DerivativeStatus(models.TextChoices):
        PENDING = "pending", "En attente"
        READY = "ready", "Disponibles"
        SKIPPED = "skipped", "Génération désactivée"
        ERROR = "error", "Erreur de génération"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    uploader = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="uploaded_media")
    media_type = models.CharField(max_length=10, choices=MediaType.choices, default=MediaType.PHOTO)
    title = models.CharField("titre", max_length=200, blank=True)
    description = models.TextField("description", max_length=5000, blank=True)
    original_name = models.CharField(max_length=255)
    original_key = models.CharField(max_length=500, blank=True)
    thumbnail_key = models.CharField(max_length=500, blank=True)
    preview_key = models.CharField(max_length=500, blank=True)
    size_bytes = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    derivative_status = models.CharField(max_length=10, choices=DerivativeStatus.choices, default=DerivativeStatus.PENDING)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    captured_at = models.DateTimeField("date EXIF", null=True, blank=True)
    taken_at_override = models.DateTimeField("date corrigée", null=True, blank=True)
    sort_date = models.DateTimeField(default=timezone.now, db_index=True)
    uploaded_at = models.DateTimeField(default=timezone.now)
    camera = models.CharField(max_length=200, blank=True, db_index=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    exif = models.JSONField(default=dict, blank=True)
    albums = models.ManyToManyField(Album, through="AlbumMedia", related_name="media_items")
    tags = models.ManyToManyField(Tag, blank=True, related_name="media_items")

    objects = MediaItemQuerySet.as_manager()

    class Meta:
        ordering = ("-sort_date", "-id")
        indexes = [models.Index(fields=("sort_date", "id"), name="media_sort_cursor")]
        constraints = [
            models.CheckConstraint(condition=Q(media_type="photo"), name="media_photo_only"),
            models.CheckConstraint(
                condition=(Q(latitude__isnull=True, longitude__isnull=True) | Q(latitude__gte=-90, latitude__lte=90, longitude__gte=-180, longitude__lte=180, latitude__isnull=False, longitude__isnull=False)),
                name="media_valid_coordinates",
            ),
        ]

    def __str__(self):
        return self.title or self.original_name

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse("library:media_detail", args=[self.pk])

    def refresh_sort_date(self):
        """Keep a manual correction authoritative across EXIF regeneration."""
        self.sort_date = self.taken_at_override or self.captured_at or self.uploaded_at
        return self.sort_date

    def save(self, *args, **kwargs):
        self.refresh_sort_date()
        fields = kwargs.get("update_fields")
        if fields is not None and {"captured_at", "taken_at_override", "uploaded_at", "sort_date"}.intersection(fields):
            kwargs["update_fields"] = set(fields) | {"sort_date"}
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if (self.latitude is None) != (self.longitude is None):
            raise ValidationError("Latitude et longitude doivent être renseignées ensemble.")
        if self.latitude is not None and (
            not math.isfinite(self.latitude) or not math.isfinite(self.longitude)
            or not -90 <= self.latitude <= 90 or not -180 <= self.longitude <= 180
        ):
            raise ValidationError("Coordonnées GPS invalides.")
        if not isinstance(self.exif, dict):
            raise ValidationError({"exif": "Les métadonnées doivent être un objet."})


class AlbumMedia(models.Model):
    album = models.ForeignKey(Album, on_delete=models.CASCADE, related_name="media_memberships")
    media = models.ForeignKey(MediaItem, on_delete=models.CASCADE, related_name="album_memberships")
    added_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("album", "media"), name="unique_album_media")]


class Favorite(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="favorites")
    media = models.ForeignKey(MediaItem, on_delete=models.CASCADE, related_name="favorites")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("user", "media"), name="unique_user_favorite")]
