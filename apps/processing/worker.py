"""Single-host supervisor, bounded children and lease-fenced publications.

No database transaction spans image work. SQLite does not implement row locks:
claims/finalizations use conditional UPDATE, never select_for_update().
"""

from contextlib import contextmanager
from datetime import timedelta
import fcntl
import multiprocessing
import os
from pathlib import Path
import time
import uuid

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.library.models import MediaItem

from .models import ProcessingJob
from .pipeline import child_main
from .services import processing_options
from .storage import media_path


LEASE_SECONDS = 60
MAX_ATTEMPTS = 3


@contextmanager
def instance_lock():
    path = Path(getattr(settings, "PROCESSING_LOCK_PATH", Path(settings.DATABASES["default"]["NAME"]).parent / "processing.lock"))
    with path.open("a+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Un worker est déjà actif sur ce volume.") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def recover_expired():
    now = timezone.now()
    expired = list(ProcessingJob.objects.filter(status="running", lease_expires_at__lte=now).order_by("lease_expires_at")[:100])
    for job in expired:
        status = "error" if job.attempts >= MAX_ATTEMPTS else "pending"
        with transaction.atomic():
            changed = ProcessingJob.objects.filter(pk=job.pk, status="running", claim_token=job.claim_token, lease_expires_at__lte=now).update(
                status=status, available_at=now, claim_token=None, lease_expires_at=None,
                finished_at=now if status == "error" else None,
                error_code="lease_expired", error_message="Le traitement a été interrompu avant sa confirmation.",
            )
            if changed and status == "error":
                mark_media_error(job)


def claim_next_job():
    now = timezone.now()
    candidate = ProcessingJob.objects.filter(status="pending", available_at__lte=now).order_by("available_at", "created_at", "pk").values_list("pk", flat=True).first()
    if candidate is None:
        return None
    token = uuid.uuid4()
    changed = ProcessingJob.objects.filter(pk=candidate, status="pending", available_at__lte=now).update(
        status="running", attempts=F("attempts") + 1, claim_token=token,
        lease_expires_at=now + timedelta(seconds=LEASE_SECONDS), started_at=now,
        finished_at=None, error_code="", error_message="",
    )
    return ProcessingJob.objects.select_related("media").get(pk=candidate, claim_token=token) if changed else None


def owned(job):
    return ProcessingJob.objects.filter(pk=job.pk, status="running", claim_token=job.claim_token, lease_expires_at__gt=timezone.now())


def mark_media_error(job):
    if job.kind == ProcessingJob.Kind.INSPECT:
        MediaItem.objects.filter(pk=job.media_id).update(status="error", derivative_status="error")
    else:
        # Existing published derivatives remain usable after failed regeneration.
        MediaItem.objects.filter(pk=job.media_id).exclude(derivative_status="ready").update(derivative_status="error")


def finish_failure(job, code, message, retryable=False):
    retry = retryable and job.attempts < MAX_ATTEMPTS
    now = timezone.now()
    with transaction.atomic():
        changed = owned(job).update(
            status="pending" if retry else "error", claim_token=None, lease_expires_at=None,
            available_at=now + timedelta(seconds=30 * 2 ** (job.attempts - 1)),
            finished_at=None if retry else now, error_code=code[:40], error_message=message[:240],
        )
        if changed and not retry:
            mark_media_error(job)
    return bool(changed)


def finish_success(job, result, keys, options):
    with transaction.atomic():
        if not owned(job).update(status="done", finished_at=timezone.now(), claim_token=None, lease_expires_at=None):
            return False
        media = MediaItem.objects.get(pk=job.media_id)
        if job.kind == ProcessingJob.Kind.INSPECT:
            # Fresh values preserve edits committed while the child was decoding.
            for field in ("width", "height", "camera", "latitude", "longitude", "exif"):
                setattr(media, field, result[field])
            media.captured_at = parse_datetime(result["captured_at"]) if result.get("captured_at") else None
            media.status = MediaItem.Status.READY
            media.derivative_status = "pending" if options["auto_generate"] else "skipped"
            media.refresh_sort_date()
            media.save(update_fields=("width", "height", "camera", "latitude", "longitude", "exif", "captured_at", "sort_date", "status", "derivative_status"))
            ProcessingJob.objects.get_or_create(
                media=media, kind="render", status="pending" if options["auto_generate"] else "skipped",
            )
        else:
            media.thumbnail_key = keys["thumbnail"]
            media.preview_key = keys["preview"]
            media.derivative_status = MediaItem.DerivativeStatus.READY
            media.save(update_fields=("thumbnail_key", "preview_key", "derivative_status"))
    return True


def discard_outputs(keys):
    for key in keys.values():
        try:
            path = media_path(key)
            path.unlink(missing_ok=True)
            path.with_suffix(".part").unlink(missing_ok=True)
        except (OSError, ValueError):
            pass


def run_claimed_job(job, stop_requested=lambda: False):
    options = processing_options()
    keys = {} if job.kind == "inspect" else {
        "thumbnail": f"thumbnails/{job.media_id}/{job.claim_token}.webp",
        "preview": f"previews/{job.media_id}/{job.claim_token}.webp",
    }
    try:
        original = str(media_path(job.media.original_key, category="originals"))
        outputs = {key: str(media_path(value)) for key, value in keys.items()}
    except ValueError:
        return finish_failure(job, "invalid_storage_key", "Le fichier source ne peut pas être ouvert.")
    if job.kind == "render" and job.media.status != "ready":
        return finish_failure(job, "not_validated", "La photo doit être validée avant sa génération.")
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=child_main, args=(send, job.kind, original, outputs, options))
    published = False
    try:
        process.start()
        send.close()
        started = last_heartbeat = time.monotonic()
        while process.is_alive():
            process.join(timeout=0.5)
            now = time.monotonic()
            if stop_requested():
                owned(job).update(status="pending", attempts=F("attempts") - 1, claim_token=None, lease_expires_at=None, available_at=timezone.now())
                return False
            if now - started > options["timeout"]:
                return finish_failure(job, "timeout", "Le traitement dépasse la durée maximale autorisée.")
            if now - last_heartbeat >= 10:
                if not owned(job).update(lease_expires_at=timezone.now() + timedelta(seconds=LEASE_SECONDS)):
                    return False
                last_heartbeat = now
        try:
            result = receive.recv() if receive.poll() else None
        except EOFError:
            result = None
        if not result or process.exitcode != 0:
            return finish_failure(job, "child_stopped", "Le processus image s’est arrêté sans résultat.", retryable=True)
        if not result["ok"]:
            return finish_failure(job, result["code"], result["message"], result["retryable"])
        published = finish_success(job, result["data"], keys, options)
        return published
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=3)
            if process.is_alive():
                process.kill()
                process.join()
        receive.close()
        send.close()
        if not published:
            discard_outputs(keys)
