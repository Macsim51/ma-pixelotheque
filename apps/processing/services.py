"""Upload admission and durable queue operations, shared by views and admin."""

import hashlib
import os
from pathlib import Path
import tempfile
import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from apps.core.models import AppSetting
from apps.library.models import Album, AlbumMedia, MediaItem

from .models import ProcessingJob
from .pipeline import FORMATS, InvalidImage, clean_text, identify
from .storage import media_path


def processing_options():
    from django.conf import settings

    config = AppSetting.load()
    return {
        "max_bytes": min(config.max_upload_mb, 100) * 1024 * 1024,
        "max_pixels": min(config.max_image_pixels, 80_000_000),
        "thumbnail_size": min(max(config.thumbnail_size, 32), 2048),
        "preview_size": min(max(config.preview_size, 128), 4096),
        "quality": min(max(config.preview_quality, 1), 95),
        "memory_mb": min(max(getattr(settings, "PROCESSING_MEMORY_MB", 768), 128), 1536),
        "timeout": min(max(getattr(settings, "PROCESSING_JOB_TIMEOUT", 120), 5), 600),
        "timezone": settings.TIME_ZONE,
        "auto_generate": config.auto_generate,
        "paused": config.worker_paused,
    }


def upload_to_album(upload, user, album):
    """Only committed, admitted files acquire DB references and inspect jobs.

    The original bytes are linked into their final UUID path without replacing
    an existing file. Admission is not publication: only INSPECT makes it ready.
    """
    if not album.can_upload(user):
        raise PermissionDenied
    options = processing_options()
    filename = clean_text(Path(upload.name.replace("\\", "/")).name, 255)
    extension = Path(filename).suffix.lower()
    if extension not in set().union(*FORMATS.values()):
        raise ValidationError("Choisissez une photo JPEG, PNG ou WebP.")
    media_id = uuid.uuid4()
    original_key = f"originals/{media_id.hex[:2]}/{media_id}{extension}"
    original = media_path(original_key, category="originals")
    temporary_root = media_path("temp")
    temporary_root.mkdir(parents=True, exist_ok=True)
    temporary = None
    linked = False
    try:
        with tempfile.NamedTemporaryFile(dir=temporary_root, prefix="upload-", delete=False) as stream:
            temporary = Path(stream.name)
            digest = hashlib.sha256()
            size = 0
            for chunk in upload.chunks(chunk_size=256 * 1024):
                size += len(chunk)
                if size > options["max_bytes"]:
                    raise ValidationError("Ce fichier dépasse la taille maximale autorisée.")
                digest.update(chunk)
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        if size == 0:
            raise ValidationError("Le fichier est vide.")
        try:
            identify(temporary, options["max_pixels"], extension=extension)
        except InvalidImage as error:
            raise ValidationError(str(error)) from None
        original.parent.mkdir(parents=True, exist_ok=True)
        os.link(temporary, original)  # Same media volume; O_EXCL semantics.
        linked = True
        os.chmod(original, 0o440)
        with transaction.atomic():
            current_album = Album.objects.get(pk=album.pk)
            current_user = get_user_model().objects.get(pk=user.pk)
            if not current_album.can_upload(current_user):
                raise PermissionDenied
            media = MediaItem.objects.create(
                id=media_id, uploader=current_user, original_name=filename,
                original_key=original_key, size_bytes=size, sha256=digest.hexdigest(),
            )
            AlbumMedia.objects.create(album=current_album, media=media)
            ProcessingJob.objects.create(media=media, kind=ProcessingJob.Kind.INSPECT)
        return media
    except Exception:
        if linked:
            original.unlink(missing_ok=True)
        raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def enqueue_media(media, force=False):
    kind = ProcessingJob.Kind.RENDER if media.status == MediaItem.Status.READY else ProcessingJob.Kind.INSPECT
    if kind == ProcessingJob.Kind.RENDER and media.derivative_status == MediaItem.DerivativeStatus.READY and not force:
        return None
    try:
        with transaction.atomic():
            job = ProcessingJob.objects.create(media=media, kind=kind)
            if kind == ProcessingJob.Kind.INSPECT:
                MediaItem.objects.filter(pk=media.pk).update(status=MediaItem.Status.PENDING)
            elif not media.thumbnail_key or not media.preview_key:
                MediaItem.objects.filter(pk=media.pk).update(derivative_status=MediaItem.DerivativeStatus.PENDING)
            return job
    except IntegrityError:
        if ProcessingJob.objects.filter(media=media, kind=kind, status__in=("pending", "running")).exists():
            return None
        raise


def retry_job(job):
    if job.status not in (ProcessingJob.Status.ERROR, ProcessingJob.Status.SKIPPED):
        return None
    return enqueue_media(job.media, force=True)


def regenerate_album(album):
    return regenerate_queryset(MediaItem.objects.filter(albums=album))


def regenerate_all():
    return regenerate_queryset(MediaItem.objects.all())


def regenerate_queryset(queryset):
    """Materialize bounded ID batches: never iterate a changing SQLite cursor."""
    count, cursor = 0, None
    while True:
        batch = queryset.order_by("pk")
        if cursor is not None:
            batch = batch.filter(pk__gt=cursor)
        items = list(batch[:100])
        if not items:
            return count
        for media in items:
            count += enqueue_media(media, force=True) is not None
        cursor = items[-1].pk
