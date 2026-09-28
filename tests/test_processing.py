"""Admission, bounded image processing, recovery and private file delivery."""

from datetime import timedelta
import hashlib
import io
from pathlib import Path
import tempfile
import uuid
from unittest.mock import patch

from PIL import Image
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.http import Http404
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AppSetting
from apps.library.models import Album, MediaItem
from apps.processing.models import ProcessingJob
from apps.processing.pipeline import captured_at, coordinate
from apps.processing.services import enqueue_media, processing_options, upload_to_album
from apps.processing.storage import media_path, media_response
from apps.processing.worker import claim_next_job, finish_failure, finish_success, instance_lock, recover_expired, run_claimed_job


def photo_bytes(format="JPEG", size=(64, 32), exif=None):
    output = io.BytesIO()
    image = Image.new("RGB", size, "cornflowerblue")
    kwargs = {"exif": exif} if exif is not None else {}
    image.save(output, format=format, **kwargs)
    image.close()
    return output.getvalue()


class ProcessingTests(TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pixel-processing-", dir="/tmp")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.override = override_settings(
            MEDIA_ROOT=self.directory / "media", PROCESSING_LOCK_PATH=self.directory / "worker.lock",
            PROCESSING_JOB_TIMEOUT=15, PROCESSING_MEMORY_MB=768,
        )
        self.override.enable()
        self.addCleanup(self.override.disable)
        users = get_user_model().objects
        self.owner = users.create_user(username="owner", role="family")
        self.other = users.create_user(username="other", role="family")
        self.guest = users.create_user(username="guest", role="guest")
        self.album = Album.objects.create(owner=self.owner, title="Vacances", visibility="private")
        self.config = AppSetting.load()

    def upload(self, *, content=None, name="photo.jpg"):
        content = photo_bytes() if content is None else content
        return upload_to_album(SimpleUploadedFile(name, content, "image/jpeg"), self.owner, self.album)

    def process_one(self):
        job = claim_next_job()
        self.assertIsNotNone(job)
        run_claimed_job(job)
        job.refresh_from_db()
        return job

    def test_upload_preserves_bytes_and_waits_for_validation(self):
        content = photo_bytes()
        media = self.upload(content=content)
        self.assertEqual(media.status, "pending")
        self.assertEqual(media.sha256, hashlib.sha256(content).hexdigest())
        self.assertEqual(media_path(media.original_key).read_bytes(), content)
        self.assertEqual(media.jobs.get().kind, "inspect")
        self.assertEqual(list(media_path("temp").iterdir()), [])
        with self.assertRaises(Http404):
            media_response(RequestFactory().get("/"), media, "original")

    def test_rejects_non_image_and_extension_mismatch(self):
        for data, name in ((b"not a photo", "test.jpg"), (photo_bytes("PNG"), "test.jpg"), (photo_bytes(), "test.svg")):
            with self.subTest(name=name), self.assertRaises(ValidationError):
                self.upload(content=data, name=name)
        self.assertFalse(MediaItem.objects.exists())
        self.assertFalse(ProcessingJob.objects.exists())

    def test_pixel_limit_and_real_byte_limit(self):
        self.config.max_image_pixels = 10
        self.config.save()
        with self.assertRaises(ValidationError):
            self.upload()
        self.config.max_image_pixels = 40_000_000
        self.config.max_upload_mb = 1
        self.config.save()
        with self.assertRaises(ValidationError):
            self.upload(content=photo_bytes() + b"x" * (1024 * 1024))
        self.assertFalse(MediaItem.objects.exists())

    def test_animated_webp_is_rejected(self):
        output = io.BytesIO()
        first, second = Image.new("RGB", (8, 8), "red"), Image.new("RGB", (8, 8), "blue")
        first.save(output, format="WEBP", save_all=True, append_images=[second], duration=100, loop=0)
        with self.assertRaises(ValidationError):
            self.upload(content=output.getvalue(), name="animation.webp")
        first.close()
        second.close()

    def test_webp_admission_never_opens_native_decoder(self):
        for lossless in (False, True):
            output = io.BytesIO()
            with Image.new("RGB", (12, 8), "red") as image:
                image.save(output, format="WEBP", lossless=lossless)
            with patch("apps.processing.pipeline.Image.open", side_effect=AssertionError("Native decoder called in web")):
                media = self.upload(content=output.getvalue(), name="photo.webp")
            self.assertEqual(media.status, "pending")

    def test_webp_oversized_canvas_is_rejected_without_native_allocation(self):
        payload = b"\0\0\0\0" + (100_000 - 1).to_bytes(3, "little") * 2
        content = b"RIFF" + (22).to_bytes(4, "little") + b"WEBPVP8X" + (10).to_bytes(4, "little") + payload
        with patch("apps.processing.pipeline.Image.open", side_effect=AssertionError("Native decoder called")):
            with self.assertRaises(ValidationError):
                self.upload(content=content, name="huge.webp")

    def test_guest_and_unpermitted_family_cannot_upload(self):
        for user in (self.guest, self.other):
            with self.subTest(user=user.username), self.assertRaises(PermissionDenied):
                upload_to_album(SimpleUploadedFile("photo.jpg", photo_bytes()), user, self.album)

    def test_permission_revocation_during_upload_removes_uncommitted_file(self):
        with patch.object(Album, "can_upload", side_effect=[True, False]):
            with self.assertRaises(PermissionDenied):
                self.upload()
        self.assertFalse(MediaItem.objects.exists())
        self.assertFalse(ProcessingJob.objects.exists())
        self.assertFalse(any(path.is_file() for path in media_path("originals").rglob("*")))

    def test_inspection_runs_even_when_generation_disabled(self):
        self.config.auto_generate = False
        self.config.save()
        media = self.upload()
        job = self.process_one()
        self.assertEqual(job.status, "done", job.error_message)
        media.refresh_from_db()
        self.assertEqual((media.status, media.derivative_status), ("ready", "skipped"))
        self.assertEqual((media.width, media.height), (64, 32))
        self.assertTrue(media.jobs.filter(kind="render", status="skipped").exists())
        self.assertIsNone(claim_next_job())

    def test_original_hash_orientation_and_exif_free_derivatives(self):
        exif = Image.Exif()
        exif[274], exif[271], exif[272], exif[306] = 6, "Maker", "Camera", "2024:05:12 14:30:00"
        original = photo_bytes(exif=exif)
        media = self.upload(content=original)
        first = self.process_one()
        self.assertEqual(first.status, "done", first.error_message)
        second = self.process_one()
        self.assertEqual(second.status, "done", second.error_message)
        media.refresh_from_db()
        self.assertEqual((media.width, media.height), (32, 64))
        self.assertEqual(media.camera, "Maker Camera")
        self.assertEqual(media.captured_at.year, 2024)
        self.assertEqual(media.derivative_status, "ready")
        self.assertEqual(media_path(media.original_key).read_bytes(), original)
        for key in (media.thumbnail_key, media.preview_key):
            with Image.open(media_path(key)) as image:
                self.assertEqual(image.size, (32, 64))
                self.assertFalse(dict(image.getexif()))

    def test_truncated_jpeg_fails_in_worker_before_publication(self):
        media = self.upload(content=photo_bytes(size=(128, 128))[:-20])
        self.assertEqual(media.status, "pending")
        job = self.process_one()
        media.refresh_from_db()
        self.assertEqual(job.status, "error")
        self.assertEqual(job.error_code, "invalid_image")
        self.assertEqual(media.status, "error")
        self.assertFalse(media.jobs.filter(kind="render").exists())

    def test_manual_date_survives_inspection(self):
        media = self.upload()
        corrected = timezone.now() - timedelta(days=100)
        MediaItem.objects.filter(pk=media.pk).update(taken_at_override=corrected, title="Mon titre")
        self.process_one()
        media.refresh_from_db()
        self.assertEqual(media.sort_date, corrected)
        self.assertEqual(media.title, "Mon titre")

    def test_one_active_job_and_claim_at_a_time(self):
        media = self.upload()
        self.assertIsNone(enqueue_media(media, force=True))
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProcessingJob.objects.create(media=media, kind="inspect")
        self.assertIsNotNone(claim_next_job())
        self.assertIsNone(claim_next_job())

    def test_expired_lease_requeues_and_invalidates_previous_owner(self):
        media = self.upload()
        old = claim_next_job()
        ProcessingJob.objects.filter(pk=old.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
        recover_expired()
        current = claim_next_job()
        self.assertNotEqual(current.claim_token, old.claim_token)
        self.assertFalse(finish_failure(old, "stale", "Old process"))
        self.assertFalse(finish_success(old, {}, {}, processing_options()))
        current.refresh_from_db()
        self.assertEqual(current.status, "running")
        media.refresh_from_db()
        self.assertEqual(media.status, "pending")

    def test_retries_are_bounded(self):
        media = self.upload()
        job = claim_next_job()
        self.assertTrue(finish_failure(job, "temporary", "Temporaire", retryable=True))
        job.refresh_from_db()
        self.assertEqual(job.status, "pending")
        self.assertGreater(job.available_at, timezone.now())
        ProcessingJob.objects.filter(pk=job.pk).update(available_at=timezone.now(), attempts=2)
        job = claim_next_job()
        finish_failure(job, "temporary", "Temporaire", retryable=True)
        job.refresh_from_db()
        self.assertEqual(job.status, "error")
        media.refresh_from_db()
        self.assertEqual(media.status, "error")

    def test_instance_lock_rejects_second_supervisor(self):
        with instance_lock():
            with self.assertRaises(RuntimeError), instance_lock():
                self.fail("Second worker entered")

    def test_private_file_response_and_missing_derivatives(self):
        media = self.upload()
        media.status = "ready"
        media.save(update_fields=("status",))
        request = RequestFactory().get("/")
        response = media_response(request, media, "original")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        # No HTTP request was opened by this direct utility test. Close only
        # its stream, without emitting Django's request_finished signal.
        response.file_to_stream.close()
        for variant, allow in (("original", False), ("preview", True), ("unknown", True)):
            with self.subTest(variant=variant), self.assertRaises(Http404):
                media_response(request, media, variant, allow_original=allow)

    def test_storage_paths_and_symlinks_are_confined(self):
        for key in (".", "../secret", "/etc/passwd", "originals/../../secret", "originals\\secret"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                media_path(key)
        self.directory.joinpath("media").mkdir(exist_ok=True)
        self.directory.joinpath("media", "link").symlink_to(self.directory)
        with self.assertRaises(ValueError):
            media_path("link/secret")

    def test_exif_helpers_handle_invalid_coordinates_and_timezones(self):
        self.assertAlmostEqual(coordinate((48, 30, 0), "N", "N", "S"), 48.5)
        self.assertIsNone(coordinate((48, 70, 0), "N", "N", "S"))
        self.assertIsNone(coordinate((float("nan"), 0, 0), "N", "N", "S"))
        self.assertEqual(captured_at({"date_text": "2024:01:02 03:04:05", "offset_text": "+02:00"}, "Europe/Paris"), "2024-01-02T03:04:05+02:00")
        self.assertIsNone(captured_at({"date_text": "invalid"}, "Europe/Paris"))

    def test_upload_json_creates_album_and_returns_individual_errors(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("processing:upload"), {
            "new_album": "Nouveau", "files": [SimpleUploadedFile("good.jpg", photo_bytes()), SimpleUploadedFile("bad.jpg", b"invalid")],
        }, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(len(result["uploaded"]), 1)
        self.assertEqual(result["errors"][0]["name"], "bad.jpg")
        album = Album.objects.get(pk=result["album_id"])
        self.assertEqual((album.owner_id, album.visibility), (self.owner.pk, "family"))

    def test_upload_view_requires_csrf_and_guest_is_forbidden(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        response = client.post(reverse("processing:upload"), {"album": str(self.album.pk), "files": SimpleUploadedFile("photo.jpg", photo_bytes())})
        self.assertEqual(response.status_code, 403)
        self.client.force_login(self.guest)
        self.assertEqual(self.client.get(reverse("processing:upload")).status_code, 403)
        self.assertFalse(MediaItem.objects.exists())

    @override_settings(FILE_UPLOAD_MAX_MEMORY_SIZE=1)
    def test_multipart_spills_to_private_media_temp(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("processing:upload"), {"album": str(self.album.pk), "files": SimpleUploadedFile("photo.jpg", photo_bytes())}, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(media_path("temp").iterdir()), [])

    def test_multipart_enforces_limit_before_storing_original(self):
        self.config.max_upload_mb = 1
        self.config.save()
        self.client.force_login(self.owner)
        response = self.client.post(reverse("processing:upload"), {"album": str(self.album.pk), "files": SimpleUploadedFile("big.jpg", photo_bytes() + b"x" * (1024 * 1024))}, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(MediaItem.objects.exists())
