from datetime import timedelta
import hashlib
import json
from unittest.mock import patch
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.core import serializers
from django.db import IntegrityError, transaction
from django.http import Http404, HttpResponse
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AppSetting
from apps.library.models import Album, AlbumMedia, Favorite, MediaItem, Tag
from apps.sharing.models import SharedLink, digest_token


User = get_user_model()


class SharingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("share-owner", role="family")
        cls.other = User.objects.create_user("other-family", role="family")
        cls.guest = User.objects.create_user("share-guest")
        cls.admin_user = User.objects.create_superuser("share-admin", password="test-superuser-password")
        cls.album = Album.objects.create(owner=cls.owner, title="Vacances partagées", visibility="private")
        cls.hidden_album = Album.objects.create(owner=cls.other, title="Album totalement confidentiel", visibility="private")
        cls.photo = MediaItem.objects.create(
            uploader=cls.owner, title="La plage", description="Une belle journée.",
            original_name="filename-location-secret.jpg", status="ready", derivative_status="ready",
            original_key="originals/test.jpg", thumbnail_key="thumbnails/test.webp", preview_key="previews/test.webp",
            latitude=48.123456, longitude=4.987654, camera="CAMERA_SECRET_NAME",
            exif={"GPS": "GPS_SECRET_VALUE"},
        )
        AlbumMedia.objects.create(album=cls.album, media=cls.photo)
        AlbumMedia.objects.create(album=cls.hidden_album, media=cls.photo)
        cls.photo.tags.add(Tag.objects.create(name="Vacances"))
        cls.hidden_photo = MediaItem.objects.create(
            uploader=cls.other, title="Photo totalement confidentielle", original_name="hidden.jpg", status="ready",
        )
        AlbumMedia.objects.create(album=cls.hidden_album, media=cls.hidden_photo)
        cls.link, cls.token = SharedLink.issue(album=cls.album, creator=cls.owner)

    def album_url(self, token=None):
        return reverse("sharing:album", kwargs={"token": token or self.token})

    def photo_url(self, photo=None):
        return reverse("sharing:photo", kwargs={"token": self.token, "media_id": (photo or self.photo).pk})

    def file_url(self, variant="preview", photo=None):
        return reverse("sharing:file", kwargs={"token": self.token, "media_id": (photo or self.photo).pk, "variant": variant})

    def manage_url(self, album=None):
        return reverse("sharing:manage", kwargs={"album_id": (album or self.album).pk})

    def action_url(self, action, album=None, link=None):
        return reverse("sharing:change_link", kwargs={"album_id": (album or self.album).pk, "link_id": (link or self.link).pk, "action": action})

    def assert_isolated(self, response, status=200):
        self.assertEqual(response.status_code, status)
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("private", response["Cache-Control"])
        self.assertIn("noindex", response["X-Robots-Tag"])
        self.assertNotContains(response, reverse("accounts:login"), status_code=status)
        self.assertNotContains(response, reverse("admin:index"), status_code=status)
        self.assertNotContains(response, self.hidden_album.title, status_code=status)
        self.assertNotContains(response, self.hidden_photo.title, status_code=status)

    def test_token_has_256_random_bits_and_only_its_digest_is_stored(self):
        self.assertEqual(len(self.token), 43)
        self.assertRegex(self.token, r"^[A-Za-z0-9_-]{43}$")
        self.assertEqual(self.link.token_digest, hashlib.sha256(self.token.encode()).hexdigest())
        self.assertNotIn(self.token, serializers.serialize("json", [self.link]))
        self.assertFalse(hasattr(self.link, "token"))
        _, other_token = SharedLink.issue(album=self.album, creator=self.owner)
        self.assertNotEqual(self.token, other_token)

    def test_malformed_tokens_are_rejected_before_lookup(self):
        for value in ("a", "a" * 42, "a" * 44, "é" * 43, "a" * 42 + "."):
            with self.subTest(value=value):
                self.assertIsNone(digest_token(value))
                self.assert_isolated(self.client.get(self.album_url(value)), status=404)

    def test_link_opens_only_its_album_without_authenticating(self):
        response = self.client.get(self.album_url())
        self.assert_isolated(response)
        self.assertContains(response, self.album.title)
        self.assertContains(response, self.photo.title)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_logged_in_superuser_does_not_broaden_public_scope(self):
        self.client.force_login(self.admin_user)
        self.assert_isolated(self.client.get(self.album_url()))
        self.assert_isolated(self.client.get(self.photo_url(self.hidden_photo)), status=404)
        with patch("apps.sharing.views.media_response") as serve:
            self.assert_isolated(self.client.get(self.file_url(photo=self.hidden_photo)), status=404)
        serve.assert_not_called()

    def test_public_metadata_excludes_other_albums_gps_exif_and_account_names(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(self.photo_url())
        self.assert_isolated(response)
        for secret in ("48.123456", "4.987654", "GPS_SECRET_VALUE", "CAMERA_SECRET_NAME", self.owner.username, self.admin_user.username, self.photo.original_name):
            self.assertNotContains(response, secret)
        self.assertContains(response, "Vacances")
        self.assertContains(response, self.photo.description)

    def test_titles_descriptions_and_tags_are_escaped(self):
        self.photo.title = "<script>alert(1)</script>"
        self.photo.description = "<img src=x onerror=alert(2)>"
        self.photo.save(update_fields=["title", "description"])
        self.photo.tags.add(Tag.objects.create(name="<svg onload=alert(3)>"))
        response = self.client.get(self.photo_url())
        self.assertNotContains(response, "<script>alert(1)")
        self.assertNotContains(response, "<img src=x")
        self.assertNotContains(response, "<svg onload=")
        self.assertContains(response, "&lt;script&gt;")

    def test_disabled_expired_and_revoked_links_deny_all_public_routes(self):
        states = (
            {"active": False, "expires_at": None, "revoked_at": None},
            {"active": True, "expires_at": timezone.now() - timedelta(seconds=1), "revoked_at": None},
            {"active": False, "expires_at": None, "revoked_at": timezone.now()},
        )
        for state in states:
            SharedLink.objects.filter(pk=self.link.pk).update(**state)
            for url in (self.album_url(), self.photo_url(), self.file_url()):
                with self.subTest(state=state, url=url), patch("apps.sharing.views.media_response") as serve:
                    self.assert_isolated(self.client.get(url), status=404)
                    serve.assert_not_called()

    def test_existing_session_does_not_preserve_access_after_revocation(self):
        self.assertEqual(self.client.get(self.album_url()).status_code, 200)
        SharedLink.objects.filter(pk=self.link.pk).update(active=False, revoked_at=timezone.now())
        self.assert_isolated(self.client.get(self.photo_url()), status=404)

    def test_membership_removal_immediately_revokes_photo_and_file_access(self):
        self.client.force_login(self.admin_user)
        AlbumMedia.objects.filter(album=self.album, media=self.photo).delete()
        self.assertEqual(self.client.get(self.album_url()).context["page_obj"].paginator.count, 0)
        self.assert_isolated(self.client.get(self.photo_url()), status=404)
        with patch("apps.sharing.views.media_response") as serve:
            self.assert_isolated(self.client.get(self.file_url()), status=404)
        serve.assert_not_called()

    def test_only_ready_photos_are_public_even_for_uploader(self):
        self.client.force_login(self.owner)
        for state in ("pending", "error"):
            MediaItem.objects.filter(pk=self.photo.pk).update(status=state)
            with self.subTest(state=state):
                self.assertEqual(self.client.get(self.album_url()).context["page_obj"].paginator.count, 0)
                self.assert_isolated(self.client.get(self.photo_url()), status=404)
                self.assert_isolated(self.client.get(self.file_url()), status=404)

    def test_disabled_original_download_is_denied_before_storage(self):
        self.client.force_login(self.admin_user)
        with patch("apps.sharing.views.media_response") as serve:
            self.assert_isolated(self.client.get(self.file_url("original")), status=404)
        serve.assert_not_called()
        self.assertNotContains(self.client.get(self.photo_url()), self.file_url("original"))

    def test_enabled_original_download_uses_the_private_file_service(self):
        SharedLink.objects.filter(pk=self.link.pk).update(allow_download=True)
        with patch("apps.sharing.views.media_response", return_value=HttpResponse(b"original", content_type="image/jpeg")) as serve:
            response = self.client.get(self.file_url("original"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        serve.assert_called_once()
        self.assertEqual(serve.call_args.args[1].pk, self.photo.pk)
        self.assertEqual(serve.call_args.args[2], "original")
        self.assertTrue(serve.call_args.kwargs["allow_original"])

    def test_preview_is_available_without_original_download_permission(self):
        with patch("apps.sharing.views.media_response", return_value=HttpResponse(b"preview", content_type="image/webp")) as serve:
            response = self.client.get(self.file_url())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(serve.call_args.kwargs["allow_original"])
        self.assertIn("no-store", response["Cache-Control"])

    def test_missing_file_gets_the_isolated_error_shell(self):
        self.client.force_login(self.admin_user)
        with patch("apps.sharing.views.media_response", side_effect=Http404):
            self.assert_isolated(self.client.get(self.file_url()), status=404)

    def test_no_unknown_file_variant_reaches_storage(self):
        with patch("apps.sharing.views.media_response") as serve:
            self.assert_isolated(self.client.get(self.file_url("exif")), status=404)
        serve.assert_not_called()

    def test_neighbor_navigation_is_stable_and_limited_to_the_shared_album(self):
        newer = MediaItem.objects.create(uploader=self.owner, title="Photo suivante dans le tri", original_name="newer.jpg", status="ready", uploaded_at=self.photo.uploaded_at + timedelta(days=1))
        older = MediaItem.objects.create(uploader=self.owner, title="Photo précédente dans le tri", original_name="older.jpg", status="ready", uploaded_at=self.photo.uploaded_at - timedelta(days=1))
        AlbumMedia.objects.create(album=self.album, media=newer)
        AlbumMedia.objects.create(album=self.album, media=older)
        response = self.client.get(self.photo_url())
        self.assertEqual(response.context["previous_photo"], newer)
        self.assertEqual(response.context["next_photo"], older)
        self.assertNotContains(response, str(self.hidden_photo.pk))

    def test_album_is_paginated_instead_of_loading_the_full_collection(self):
        photos = [MediaItem(uploader=self.owner, original_name=f"photo-{number}.jpg", status="ready") for number in range(65)]
        MediaItem.objects.bulk_create(photos)
        AlbumMedia.objects.bulk_create([AlbumMedia(album=self.album, media=photo) for photo in photos])
        first = self.client.get(self.album_url())
        second = self.client.get(self.album_url() + "?page=2")
        self.assertEqual(first.context["page_obj"].paginator.count, 66)
        self.assertEqual(len(first.context["page_obj"]), 60)
        self.assertEqual(len(second.context["page_obj"]), 6)
        self.assertFalse({photo.pk for photo in first.context["page_obj"]} & {photo.pk for photo in second.context["page_obj"]})

    def test_access_count_tracks_album_gets_not_images_or_head(self):
        self.client.get(self.album_url())
        self.client.head(self.album_url())
        self.client.get(self.photo_url())
        with patch("apps.sharing.views.media_response", return_value=HttpResponse(b"preview")):
            self.client.get(self.file_url())
        self.link.refresh_from_db()
        self.assertEqual(self.link.access_count, 1)
        self.assertIsNotNone(self.link.last_accessed_at)

    def test_share_visit_does_not_grant_global_favorites_timeline_or_map_access(self):
        self.client.get(self.album_url())
        for name in ("library:album_list", "library:timeline", "library:favorites", "library:map"):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.url.startswith(reverse("accounts:login")))
        self.assertEqual(Favorite.objects.count(), 0)

    def test_unknown_public_paths_never_show_global_navigation(self):
        self.client.force_login(self.admin_user)
        for path in (self.album_url() + "favorite/", self.album_url() + "photos/not-a-uuid/", "/s/not/a/link/"):
            self.assert_isolated(self.client.get(path), status=404)

    def test_public_routes_cannot_mutate_content(self):
        for url in (self.album_url(), self.photo_url(), self.file_url()):
            response = self.client.post(url, {"title": "changed"})
            self.assertEqual(response.status_code, 405)
            self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.photo.refresh_from_db()
        self.assertEqual(self.photo.title, "La plage")

    def test_management_requires_authentication(self):
        response = self.client.get(self.manage_url())
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse("accounts:login")))

    def test_only_owner_family_or_admin_can_manage_shares(self):
        for actor in (self.other, self.guest):
            self.client.force_login(actor)
            self.assertIn(self.client.get(self.manage_url()).status_code, (403, 404))
            self.assertIn(self.client.post(self.action_url("revoke")).status_code, (403, 404))
        self.link.refresh_from_db()
        self.assertTrue(self.link.active)

    def test_owner_demoted_to_guest_cannot_manage_sharing(self):
        User.objects.filter(pk=self.owner.pk).update(role="guest")
        self.owner.refresh_from_db()
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.manage_url()).status_code, 403)
        self.assertEqual(self.client.post(self.action_url("revoke")).status_code, 403)
        self.link.refresh_from_db()
        self.assertTrue(self.link.active)

    def test_family_sharing_setting_is_checked_on_every_management_request(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.manage_url()).status_code, 200)
        configuration = AppSetting.load()
        configuration.allow_family_sharing = False
        configuration.save(update_fields=["allow_family_sharing"])
        before = SharedLink.objects.count()
        self.assertEqual(self.client.post(self.manage_url(), {}).status_code, 403)
        self.assertEqual(SharedLink.objects.count(), before)
        self.client.force_login(self.admin_user)
        self.assertEqual(self.client.post(self.manage_url(), {}).status_code, 200)
        self.assertEqual(SharedLink.objects.count(), before + 1)

    def test_new_token_is_displayed_once_and_never_saved_in_session(self):
        self.client.force_login(self.owner)
        response = self.client.post(self.manage_url(), {"allow_download": "on"})
        self.assertEqual(response.status_code, 200)
        created_url = response.context["created_url"]
        self.assertTrue(created_url.startswith("http://testserver/"))
        token = urlsplit(created_url).path.rstrip("/").rsplit("/", 1)[1]
        created = SharedLink.objects.get(token_digest=digest_token(token))
        self.assertEqual(created.album, self.album)
        self.assertTrue(created.allow_download)
        self.assertNotIn(token, json.dumps(dict(self.client.session)))
        self.assertNotIn(token, serializers.serialize("json", [created]))
        self.assertNotContains(self.client.get(self.manage_url()), token)

    def test_expiration_in_the_past_is_rejected(self):
        self.client.force_login(self.owner)
        response = self.client.post(self.manage_url(), {"expires_at": "2001-01-01T12:00"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("expires_at", response.context["form"].errors)
        self.assertEqual(SharedLink.objects.count(), 1)

    def test_management_posts_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        self.assertEqual(client.post(self.manage_url(), {}).status_code, 403)
        self.assertEqual(client.post(self.action_url("revoke")).status_code, 403)
        self.link.refresh_from_db()
        self.assertTrue(self.link.active)

    def test_link_actions_are_post_only(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.action_url("revoke")).status_code, 405)
        self.link.refresh_from_db()
        self.assertTrue(self.link.active)

    def test_disable_can_be_reversed_but_revocation_is_final(self):
        self.client.force_login(self.owner)
        self.client.post(self.action_url("disable"))
        self.assertEqual(self.client.get(self.album_url()).status_code, 404)
        self.client.post(self.action_url("enable"))
        self.assertEqual(self.client.get(self.album_url()).status_code, 200)
        self.client.post(self.action_url("revoke"))
        self.client.post(self.action_url("enable"))
        self.link.refresh_from_db()
        self.assertFalse(self.link.active)
        self.assertIsNotNone(self.link.revoked_at)
        self.assertEqual(self.client.get(self.album_url()).status_code, 404)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SharedLink.objects.filter(pk=self.link.pk).update(active=True)

    def test_expired_link_cannot_be_reenabled(self):
        SharedLink.objects.filter(pk=self.link.pk).update(active=False, expires_at=timezone.now() - timedelta(seconds=1))
        self.client.force_login(self.owner)
        self.client.post(self.action_url("enable"))
        self.link.refresh_from_db()
        self.assertFalse(self.link.active)

    def test_managing_one_album_does_not_grant_control_over_other_links(self):
        other_link, _ = SharedLink.issue(album=self.hidden_album, creator=self.other)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(self.action_url("revoke", link=other_link)).status_code, 404)
        other_link.refresh_from_db()
        self.assertTrue(other_link.active)
