from datetime import datetime, timedelta, timezone as datetime_timezone
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.db.models import ProtectedError
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.core.models import AppSetting
from apps.library.forms import MediaItemForm
from apps.library.models import Album, AlbumMedia, AlbumPermission, Favorite, MediaItem, Tag
from apps.library.permissions import can_edit_media


UTC = datetime_timezone.utc


class MediaFixtures(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user(username="photo-owner", role="family")
        cls.viewer = User.objects.create_user(username="photo-viewer", role="family")
        cls.guest = User.objects.create_user(username="photo-guest", role="guest")
        cls.outsider = User.objects.create_user(username="photo-outsider", role="guest")
        cls.admin = User.objects.create_superuser(username="photo-admin", password=None)
        cls.family = Album.objects.create(owner=cls.owner, title="Vacances publiques famille")
        cls.private = Album.objects.create(owner=cls.owner, title="Confidentiel MariageZX42", visibility="private")
        cls.restricted = Album.objects.create(owner=cls.owner, title="Invités autorisés", visibility="restricted")
        AlbumPermission.objects.create(album=cls.restricted, user=cls.guest)
        cls.photo = cls.make_photo("Paysage", cls.family, latitude=48.85, longitude=2.35, camera="Appareil familial")
        cls.hidden = cls.make_photo("Photo privée", cls.private, latitude=50, longitude=4)
        cls.shared = cls.make_photo("Photo invitée", cls.restricted, latitude=40, longitude=-74)
        cls.multi = cls.make_photo("Plusieurs albums", cls.family, latitude=0, longitude=0)
        AlbumMedia.objects.create(media=cls.multi, album=cls.private)
        cls.pending = cls.make_photo("En cours", cls.family, status="pending")
        cls.failed = cls.make_photo("Échec", cls.family, status="error")
        cls.no_access_upload = cls.make_photo("Ancien upload retiré", cls.private, uploader=cls.viewer)
        AppSetting.load()

    @classmethod
    def make_photo(cls, title, album, **changes):
        values = {
            "title": title, "original_name": "souvenir.jpg", "uploader": cls.owner,
            "status": "ready", "derivative_status": "ready",
            "original_key": "originals/test.jpg", "thumbnail_key": "thumbnails/test.webp",
            "preview_key": "previews/test.webp", "width": 1200, "height": 800,
            "captured_at": datetime(2024, 6, 15, 12, tzinfo=UTC),
        }
        values.update(changes)
        media = MediaItem.objects.create(**values)
        AlbumMedia.objects.create(media=media, album=album)
        return media

    def ids_from(self, response):
        return {media.pk for media in response.context["media_items"]}


class MediaAccessTests(MediaFixtures):
    def test_visibility_uses_album_access_and_processing_state(self):
        cases = (
            (self.owner, {self.photo.pk, self.hidden.pk, self.shared.pk, self.multi.pk, self.pending.pk, self.failed.pk, self.no_access_upload.pk}),
            (self.viewer, {self.photo.pk, self.multi.pk}),
            (self.guest, {self.shared.pk}),
            (self.outsider, set()),
            (self.admin, {self.photo.pk, self.hidden.pk, self.shared.pk, self.multi.pk, self.pending.pk, self.failed.pk, self.no_access_upload.pk}),
        )
        for user, expected in cases:
            with self.subTest(user=user):
                actual = list(MediaItem.objects.visible_to(user).values_list("pk", flat=True))
                self.assertEqual(set(actual), expected)
                self.assertEqual(len(actual), len(expected))

    def test_uploader_does_not_bypass_album_permissions(self):
        self.client.force_login(self.viewer)
        self.assertFalse(can_edit_media(self.viewer, self.no_access_upload))
        for route in ("media_detail", "media_edit"):
            self.assertEqual(self.client.get(reverse(f"library:{route}", args=[self.no_access_upload.pk])).status_code, 404)

    def test_guests_cannot_access_any_global_photo_endpoint(self):
        self.client.force_login(self.guest)
        for name, args in (
            ("timeline", ()), ("timeline_year", (2024,)), ("timeline_month", (2024, 6)),
            ("timeline_day", (2024, 6, 15)), ("search", ()), ("map", ()),
            ("map_data", ()), ("map_photos", ()),
        ):
            with self.subTest(route=name):
                self.assertEqual(self.client.get(reverse(f"library:{name}", args=args)).status_code, 403)

    def test_album_placeholders_are_only_visible_to_uploader_and_admin(self):
        for user, expected in ((self.owner, 4), (self.viewer, 2), (self.admin, 4)):
            self.client.force_login(user)
            response = self.client.get(reverse("library:album_detail", args=[self.family.pk]))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["page_obj"].paginator.count, expected)
            self.assertEqual(self.pending.pk in self.ids_from(response), user != self.viewer)

    def test_album_cover_and_count_use_visible_photos_without_per_album_queries(self):
        self.client.force_login(self.viewer)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("library:album_list"))
        album = next(album for album in response.context["page_obj"] if album.pk == self.family.pk)
        self.assertEqual(album.media_count, 2)
        self.assertIn(album.cover_id, {self.photo.pk, self.multi.pk})
        self.assertLessEqual(len(queries), 8)

    def test_all_file_variants_recheck_access_before_opening_storage(self):
        self.client.force_login(self.viewer)
        with patch("apps.processing.storage.media_response", return_value=HttpResponse("image")) as delivery:
            for media in (self.hidden, self.pending, self.failed, self.no_access_upload):
                for variant in ("original", "preview", "thumbnail"):
                    with self.subTest(media=media.pk, variant=variant):
                        response = self.client.get(reverse("library:media_file", args=[media.pk, variant]))
                        self.assertEqual(response.status_code, 404)
            delivery.assert_not_called()
            response = self.client.get(reverse("library:media_file", args=[self.photo.pk, "preview"]))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(delivery.call_count, 1)

    def test_even_uploader_and_admin_cannot_open_unvalidated_file(self):
        with patch("apps.processing.storage.media_response") as delivery:
            for user in (self.owner, self.admin):
                self.client.force_login(user)
                self.assertEqual(self.client.get(reverse("library:media_file", args=[self.pending.pk, "original"])).status_code, 404)
            delivery.assert_not_called()

    def test_guest_can_view_derivative_but_cannot_download_original(self):
        self.client.force_login(self.guest)
        with tempfile.TemporaryDirectory(prefix="pixel-media-test-", dir="/tmp") as directory, override_settings(MEDIA_ROOT=directory):
            for key in (self.shared.original_key, self.shared.preview_key):
                path = Path(directory) / key
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"disposable-test-file")
            original = self.client.get(reverse("library:media_file", args=[self.shared.pk, "original"]))
            self.assertEqual(original.status_code, 403)
            preview = self.client.get(reverse("library:media_file", args=[self.shared.pk, "preview"]))
            self.assertEqual(preview.status_code, 200)
            self.assertEqual(preview["Cache-Control"], "private, no-store")
            self.assertEqual(b"".join(preview.streaming_content), b"disposable-test-file")

    def test_hidden_album_names_do_not_appear_on_visible_photo(self):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("library:media_detail", args=[self.multi.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.family.title)
        self.assertNotContains(response, self.private.title)
        self.assertNotContains(response, self.multi.original_key)

    def test_lightbox_neighbors_stay_inside_chosen_album(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("library:media_detail", args=[self.photo.pk]), {"album": self.family.pk})
        neighbors = [response.context["previous_media"], response.context["next_media"]]
        for media in filter(None, neighbors):
            self.assertTrue(media.albums.filter(pk=self.family.pk).exists())
        self.assertNotIn(self.hidden, neighbors)
        self.assertNotIn(self.shared, neighbors)

    def test_lightbox_rejects_invalid_hidden_or_unrelated_album_context(self):
        self.client.force_login(self.viewer)
        for album in ("invalid-uuid", self.private.pk, self.restricted.pk):
            with self.subTest(album=album):
                self.assertEqual(self.client.get(reverse("library:media_detail", args=[self.photo.pk]), {"album": album}).status_code, 404)

    def test_lightbox_search_context_cannot_navigate_outside_results(self):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("library:media_detail", args=[self.photo.pk]), {"context": "search", "q": "Paysage"})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["previous_media"])
        self.assertIsNone(response.context["next_media"])
        self.assertEqual(self.client.get(reverse("library:media_detail", args=[self.multi.pk]), {"context": "search", "q": "Paysage"}).status_code, 404)


class MediaEditingTests(MediaFixtures):
    def test_edit_permission_matrix_and_admin_setting(self):
        for user, expected in ((self.owner, True), (self.viewer, False), (self.guest, False), (self.admin, True)):
            self.assertEqual(can_edit_media(user, self.photo), expected)
        config = AppSetting.load()
        config.allow_family_edit = False
        config.save()
        self.assertFalse(can_edit_media(self.owner, self.photo))
        self.assertTrue(can_edit_media(self.admin, self.photo))

    def test_metadata_form_ignores_private_pipeline_fields_and_normalizes_tags(self):
        form = MediaItemForm({
            "title": "Nouvelle légende", "description": "Une journée ensemble",
            "taken_at_override": "2023-02-15T14:30", "tags_input": "Famille, famille, ÉTÉ",
            "albums": [self.family.pk],
            "uploader": self.viewer.pk, "latitude": "-80", "camera": "Appareil inventé",
            "original_key": "../private", "captured_at": "2020-01-01T00:00",
        }, user=self.owner, instance=self.photo)
        self.assertTrue(form.is_valid(), form.errors)
        media = form.save()
        self.assertEqual(media.title, "Nouvelle légende")
        self.assertEqual(media.uploader, self.owner)
        self.assertEqual(media.original_key, "originals/test.jpg")
        self.assertEqual(media.latitude, 48.85)
        self.assertEqual(media.camera, "Appareil familial")
        self.assertEqual(media.captured_at.year, 2024)
        self.assertEqual(media.sort_date, media.taken_at_override)
        self.assertEqual(set(media.tags.values_list("normalized", flat=True)), {"famille", "été"})

    def test_date_precedence_and_clearing_manual_correction(self):
        self.photo.taken_at_override = datetime(2022, 1, 1, tzinfo=UTC)
        self.photo.save(update_fields=["taken_at_override"])
        self.photo.captured_at = datetime(2025, 1, 1, tzinfo=UTC)
        self.photo.save(update_fields=["captured_at"])
        self.photo.refresh_from_db()
        self.assertEqual(self.photo.sort_date.year, 2022)
        self.photo.taken_at_override = None
        self.photo.save(update_fields=["taken_at_override"])
        self.photo.refresh_from_db()
        self.assertEqual(self.photo.sort_date.year, 2025)

    def test_metadata_edit_keeps_recent_worker_fields(self):
        form = MediaItemForm({"title": "Titre récent", "description": "", "taken_at_override": "", "tags_input": "", "albums": [self.family.pk]}, user=self.owner, instance=self.photo)
        self.assertTrue(form.is_valid(), form.errors)
        captured = datetime(2020, 2, 3, tzinfo=UTC)
        MediaItem.objects.filter(pk=self.photo.pk).update(camera="EXIF récent", captured_at=captured)
        form.save()
        self.photo.refresh_from_db()
        self.assertEqual(self.photo.camera, "EXIF récent")
        self.assertEqual(self.photo.sort_date, captured)

    def test_invalid_date_and_excessive_tags_do_not_modify_media(self):
        for values in (
            {"taken_at_override": "date-invalide"},
            {"taken_at_override": "0001-01-01T00:00"},
            {"taken_at_override": "9999-12-31T23:59"},
            {"tags_input": ",".join(f"tag-{i}" for i in range(21))},
            {"tags_input": "x" * 101},
            {"description": "x" * 5001},
        ):
            with self.subTest(values=list(values)):
                data = {"title": "Refusé", "description": "", "tags_input": "", "taken_at_override": "", "albums": [self.family.pk]}
                data.update(values)
                form = MediaItemForm(data, user=self.owner, instance=self.photo)
                self.assertFalse(form.is_valid())
        self.photo.refresh_from_db()
        self.assertEqual(self.photo.title, "Paysage")

    def test_other_family_member_cannot_mutate_metadata(self):
        self.client.force_login(self.viewer)
        response = self.client.post(reverse("library:media_edit", args=[self.photo.pk]), {"title": "Non autorisé"})
        self.assertEqual(response.status_code, 403)
        self.photo.refresh_from_db()
        self.assertEqual(self.photo.title, "Paysage")

    def test_nonfinite_and_incomplete_gps_are_rejected(self):
        for latitude, longitude in ((48, None), (None, 2), (float("inf"), 2), (48, float("nan")), (91, 2), (48, 181)):
            with self.subTest(latitude=latitude, longitude=longitude), self.assertRaises(ValidationError):
                self.photo.latitude, self.photo.longitude = latitude, longitude
                self.photo.full_clean()

    def test_photo_can_belong_to_multiple_authorized_albums(self):
        form = MediaItemForm({
            "title": "Plusieurs souvenirs", "description": "", "taken_at_override": "", "tags_input": "",
            "albums": [self.family.pk, self.private.pk],
        }, user=self.owner, instance=self.photo)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(set(self.photo.albums.values_list("pk", flat=True)), {self.family.pk, self.private.pk})

    def test_crafted_destination_without_contribution_permission_is_rejected(self):
        own_photo = self.make_photo("Mon upload", self.family, uploader=self.viewer)
        form = MediaItemForm({
            "title": "Inchangé", "description": "", "taken_at_override": "", "tags_input": "",
            "albums": [self.family.pk, self.private.pk],
        }, user=self.viewer, instance=own_photo)
        self.assertFalse(form.is_valid())
        self.assertIn("albums", form.errors)
        self.assertEqual(list(own_photo.albums.values_list("pk", flat=True)), [self.family.pk])

    def test_metadata_form_cannot_remove_last_album(self):
        form = MediaItemForm({
            "title": "Sans album", "description": "", "taken_at_override": "", "tags_input": "", "albums": [],
        }, user=self.owner, instance=self.photo)
        self.assertFalse(form.is_valid())
        self.assertIn("albums", form.errors)
        self.assertEqual(self.photo.albums.count(), 1)

    def test_contributor_preserves_hidden_and_nonwritable_existing_associations(self):
        own_photo = self.make_photo("Mon upload", self.family, uploader=self.viewer)
        locked = Album.objects.create(owner=self.owner, title="Contributions fermées", allow_family_uploads=False)
        destination = Album.objects.create(owner=self.viewer, title="Mon album personnel", visibility="private")
        AlbumMedia.objects.create(album=self.private, media=own_photo)
        AlbumMedia.objects.create(album=locked, media=own_photo)
        form = MediaItemForm({
            "title": "Reclassée", "description": "", "taken_at_override": "", "tags_input": "",
            "albums": [destination.pk],
        }, user=self.viewer, instance=own_photo)
        self.assertNotIn(self.private.title, str(form["albums"]))
        self.assertNotIn(locked.title, str(form["albums"]))
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(set(own_photo.albums.values_list("pk", flat=True)), {self.private.pk, locked.pk, destination.pk})

    def test_destination_permissions_are_checked_again_before_save(self):
        own_photo = self.make_photo("Mon upload", self.family, uploader=self.viewer)
        destination = Album.objects.create(owner=self.owner, title="Destination familiale")
        form = MediaItemForm({
            "title": "Ne doit pas être enregistré", "description": "", "taken_at_override": "", "tags_input": "",
            "albums": [self.family.pk, destination.pk],
        }, user=self.viewer, instance=own_photo)
        self.assertTrue(form.is_valid(), form.errors)
        destination.allow_family_uploads = False
        destination.save(update_fields=["allow_family_uploads"])
        with self.assertRaises(PermissionDenied):
            form.save()
        own_photo.refresh_from_db()
        self.assertEqual(own_photo.title, "Mon upload")
        self.assertEqual(list(own_photo.albums.values_list("pk", flat=True)), [self.family.pk])


class FavoriteTests(MediaFixtures):
    def test_favorite_is_personal_and_toggle_is_post_only(self):
        self.client.force_login(self.viewer)
        url = reverse("library:favorite_toggle", args=[self.photo.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertTrue(Favorite.objects.filter(user=self.viewer, media=self.photo).exists())
        self.client.force_login(self.owner)
        self.assertEqual(self.ids_from(self.client.get(reverse("library:favorites"))), set())
        self.client.force_login(self.viewer)
        self.assertEqual(self.ids_from(self.client.get(reverse("library:favorites"))), {self.photo.pk})
        self.client.post(url)
        self.assertFalse(Favorite.objects.filter(user=self.viewer, media=self.photo).exists())

    def test_revocation_removes_favorite_from_results_and_blocks_toggle(self):
        Favorite.objects.create(user=self.guest, media=self.shared)
        self.client.force_login(self.guest)
        self.assertEqual(self.ids_from(self.client.get(reverse("library:favorites"))), {self.shared.pk})
        AlbumPermission.objects.filter(album=self.restricted, user=self.guest).delete()
        self.assertEqual(self.ids_from(self.client.get(reverse("library:favorites"))), set())
        self.assertEqual(self.client.post(reverse("library:favorite_toggle", args=[self.shared.pk])).status_code, 404)

    def test_favorite_csrf_and_redirect_validation(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        url = reverse("library:favorite_toggle", args=[self.photo.pk])
        self.assertEqual(client.post(url).status_code, 403)
        self.client.force_login(self.owner)
        response = self.client.post(url, {"next": "https://attacker.example.test/"})
        self.assertEqual(response.url, reverse("library:media_detail", args=[self.photo.pk]))

    def test_htmx_favorite_returns_only_updated_form_and_disables_caching(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("library:favorite_toggle", args=[self.photo.pk]), HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'aria-pressed="true"')
        self.assertNotContains(response, "<!doctype")
        self.assertEqual(response["Cache-Control"], "private, no-store")


class SearchAndMapTests(MediaFixtures):
    def setUp(self):
        self.client.force_login(self.viewer)

    def test_search_never_matches_hidden_album_metadata(self):
        response = self.client.get(reverse("library:search"), {"q": "MariageZX42"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.ids_from(response), set())

    def test_search_matches_visible_album_and_normalized_tags(self):
        tag = Tag.objects.create(name="ÉTÉ", normalized="été")
        self.photo.tags.add(tag)
        for q, expected in (("Vacances", {self.photo.pk, self.multi.pk}), ("été", {self.photo.pk}), ("PAYSAGE", {self.photo.pk})):
            with self.subTest(q=q):
                response = self.client.get(reverse("library:search"), {"q": q})
                self.assertEqual(self.ids_from(response), expected)

    def test_search_filters_date_camera_uploader_gps_and_favorites(self):
        Favorite.objects.create(user=self.viewer, media=self.photo)
        response = self.client.get(reverse("library:search"), {
            "date_from": "2024-06-01", "date_to": "2024-06-30", "camera": "familial",
            "uploader": self.owner.pk, "gps": "yes", "favorites": "on",
        })
        self.assertEqual(self.ids_from(response), {self.photo.pk})
        response = self.client.get(reverse("library:search"), {"gps": "no"})
        self.assertEqual(self.ids_from(response), set())

    def test_invalid_search_range_is_form_error_without_results(self):
        response = self.client.get(reverse("library:search"), {"date_from": "2025-01-01", "date_to": "2024-01-01"})
        self.assertTrue(response.context["search_form"].errors)
        self.assertEqual(self.ids_from(response), set())

    def test_maximum_calendar_date_is_rejected_without_overflow(self):
        for field, value in (("date_to", "9999-12-31"), ("date_from", "0001-01-01")):
            with self.subTest(field=field, value=value):
                response = self.client.get(reverse("library:search"), {field: value})
                self.assertEqual(response.status_code, 200)
                self.assertIn(field, response.context["search_form"].errors)
                self.assertEqual(self.ids_from(response), set())

    def test_world_map_counts_only_authorized_ready_photos_once(self):
        response = self.client.get(reverse("library:map_data"), {"bbox": "-180,-90,180,90", "zoom": "2"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 2)
        self.assertEqual(sum(cell["count"] for cell in response.json()["cells"]), 2)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertNotContains(response, str(self.hidden.pk))

    def test_map_and_lightbox_preserve_antimeridian_region(self):
        east = self.make_photo("Est", self.family, latitude=10, longitude=179)
        west = self.make_photo("Ouest", self.family, latitude=10, longitude=-179)
        bounds = "170,0,-170,20"
        response = self.client.get(reverse("library:map_data"), {"bbox": bounds})
        self.assertEqual(response.json()["total"], 2)
        photos = self.client.get(reverse("library:map_photos"), {"bbox": bounds})
        self.assertEqual(self.ids_from(photos), {east.pk, west.pk})
        detail = self.client.get(reverse("library:media_detail", args=[east.pk]), {"context": "map", "bbox": bounds})
        neighbors = {media.pk for media in (detail.context["previous_media"], detail.context["next_media"]) if media}
        self.assertTrue(neighbors.issubset({west.pk}))

    def test_invalid_map_bounds_and_zoom_are_rejected(self):
        for bounds in ("bad", "nan,0,10,10", "0,0,inf,10", "-181,0,0,10", "0,20,10,10", "0,0,0,10", "0,0,5e-324,10"):
            with self.subTest(bounds=bounds):
                self.assertEqual(self.client.get(reverse("library:map_data"), {"bbox": bounds}).status_code, 400)
        self.assertEqual(self.client.get(reverse("library:map_data"), {"zoom": "999"}).status_code, 400)

    def test_map_page_sends_referrer_origin_for_external_tiles(self):
        response = self.client.get(reverse("library:map"))
        self.assertEqual(response["Referrer-Policy"], "strict-origin-when-cross-origin")


class AlbumDeletionTests(MediaFixtures):
    def test_last_album_cannot_be_deleted(self):
        with self.assertRaises(ProtectedError):
            self.family.delete()
        self.assertTrue(Album.objects.filter(pk=self.family.pk).exists())
        self.assertTrue(self.photo.albums.filter(pk=self.family.pk).exists())

    def test_album_deletion_preserves_photo_in_another_album(self):
        secondary = Album.objects.create(owner=self.owner, title="Second album")
        AlbumMedia.objects.create(album=secondary, media=self.photo)
        secondary.delete()
        self.assertTrue(MediaItem.objects.filter(pk=self.photo.pk).exists())
        self.assertTrue(self.photo.albums.filter(pk=self.family.pk).exists())

    def test_bulk_deletion_cannot_remove_all_remaining_associations(self):
        first = Album.objects.create(owner=self.owner, title="Premier")
        second = Album.objects.create(owner=self.owner, title="Second")
        photo = self.make_photo("Partagée entre les deux", first)
        AlbumMedia.objects.create(album=second, media=photo)
        with self.assertRaises(ProtectedError):
            Album.objects.filter(pk__in=[first.pk, second.pk]).delete()
        self.assertEqual(photo.albums.count(), 2)


class MediaAdminTests(MediaFixtures):
    def test_regeneration_action_is_admin_only_and_file_deletion_is_unavailable(self):
        model_admin = admin.site._registry[MediaItem]
        request = RequestFactory().get("/admin/")
        request.user = self.admin
        self.assertIn("regenerate_selected", model_admin.get_actions(request))
        self.assertNotIn("delete_selected", model_admin.get_actions(request))
        request.user = self.viewer
        self.assertNotIn("regenerate_selected", model_admin.get_actions(request))

    def test_album_regeneration_enqueues_shared_photo_only_once(self):
        from apps.processing.models import ProcessingJob

        model_admin = admin.site._registry[Album]
        request = RequestFactory().post("/admin/")
        request.user = self.admin
        selected = Album.objects.filter(pk__in=[self.family.pk, self.private.pk])
        with patch.object(model_admin, "message_user"):
            model_admin.regenerate_selected(request, selected)
        self.assertEqual(ProcessingJob.objects.filter(media=self.multi).count(), 1)
        self.assertEqual(ProcessingJob.objects.count(), 6)


class MediaVolumeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="volume", role="family")
        cls.album = Album.objects.create(owner=cls.user, title="Volume de 5 000 photos")
        start = datetime(2024, 1, 1, tzinfo=UTC)
        photos = [MediaItem(
            uploader=cls.user, original_name=f"photo-{index}.jpg", title=f"Souvenir {index}",
            status="ready", derivative_status="ready", thumbnail_key="thumbnails/fixture.webp",
            uploaded_at=start + timedelta(hours=index), sort_date=start + timedelta(hours=index),
            latitude=-79 + index % 158, longitude=-179 + index % 358,
        ) for index in range(5000)]
        MediaItem.objects.bulk_create(photos, batch_size=250)
        AlbumMedia.objects.bulk_create([AlbumMedia(album=cls.album, media=photo) for photo in photos], batch_size=250)
        AppSetting.load()

    def setUp(self):
        self.client.force_login(self.user)

    def test_5000_photo_timeline_and_album_return_only_sixty_rows(self):
        for url in (reverse("library:timeline"), reverse("library:album_detail", args=[self.album.pk])):
            with self.subTest(url=url), CaptureQueriesContext(connection) as queries:
                response = self.client.get(url)
                self.assertEqual(response.context["page_obj"].paginator.count, 5000)
                self.assertEqual(len(response.context["media_items"]), 60)
                self.assertLessEqual(len(queries), 12)
                self.assertTrue(any("LIMIT 60" in item["sql"] for item in queries.captured_queries))

    def test_period_samples_are_stable_and_bounded_by_subperiods(self):
        for route, args, maximum in (
            ("timeline_year", [2024], 12), ("timeline_month", [2024, 1], 31), ("timeline_day", [2024, 1, 1], 24),
        ):
            with self.subTest(route=route):
                url = reverse(f"library:{route}", args=args)
                with CaptureQueriesContext(connection) as queries:
                    first = self.client.get(url)
                second = self.client.get(url)
                first_ids = [media.pk for media in first.context["media_items"]]
                self.assertEqual(first_ids, [media.pk for media in second.context["media_items"]])
                self.assertLessEqual(len(first_ids), maximum)
                self.assertLessEqual(len(queries), maximum + 8)
                self.assertNotIn("RANDOM()", " ".join(query["sql"] for query in queries))

    def test_all_period_photos_are_paginated_with_disjoint_pages(self):
        url = reverse("library:timeline_month", args=[2024, 1])
        first = self.client.get(url, {"all": "1"})
        second = self.client.get(url, {"all": "1", "page": "2"})
        first_ids = {media.pk for media in first.context["media_items"]}
        second_ids = {media.pk for media in second.context["media_items"]}
        self.assertEqual(len(first_ids), 60)
        self.assertEqual(len(second_ids), 60)
        self.assertFalse(first_ids & second_ids)

    def test_5000_photo_map_is_aggregated_to_at_most_300_cells(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("library:map_data"), {"bbox": "-180,-90,180,90"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 5000)
        self.assertLessEqual(len(response.json()["cells"]), 300)
        self.assertLessEqual(len(queries), 8)
        self.assertLess(len(response.content), 100_000)

    def test_impossible_periods_return_404(self):
        for args in ((2024, 0), (2024, 13), (9999, 1), (1, 1)):
            self.assertEqual(self.client.get(reverse("library:timeline_month", args=args)).status_code, 404)
        self.assertEqual(self.client.get(reverse("library:timeline_day", args=[2024, 2, 30])).status_code, 404)
