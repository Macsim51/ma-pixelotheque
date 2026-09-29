from datetime import timedelta
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import get_script_prefix, reverse, set_script_prefix

from apps.library.covers import AlbumCovers
from apps.library.forms import AlbumCoverForm, AlbumForm
from apps.library.models import Album, AlbumMedia, AlbumPermission, MediaItem
from tests.test_media import MediaFixtures


class CoverFixtures(MediaFixtures):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.group = Album.objects.create(owner=cls.owner, title="Les zoos")
        cls.branch = Album.objects.create(owner=cls.owner, title="Europe", parent=cls.group)
        for album, parent in ((cls.family, cls.branch), (cls.private, cls.group), (cls.restricted, cls.group)):
            album.parent = parent
            album.save()
        AlbumPermission.objects.create(album=cls.group, user=cls.guest)
        cls.outside = Album.objects.create(owner=cls.owner, title="Hors des zoos")
        cls.outside_photo = cls.make_photo("Photo hors du dossier", cls.outside)

    def effective_cover(self, album, user=None):
        album.refresh_from_db()
        AlbumCovers(user or self.owner).apply([album])
        return album.cover_id

    def choose(self, album, photo, user=None):
        form = AlbumCoverForm({"cover_photo": photo.pk if photo else ""}, user=user or self.owner, album=album)
        self.assertTrue(form.is_valid(), form.errors)
        return form.save()


class AlbumCoverSelectionTests(CoverFixtures):
    def test_simple_album_keeps_most_recent_automatic_photo(self):
        self.photo.captured_at += timedelta(days=1)
        self.photo.save()
        self.assertEqual(self.effective_cover(self.family), self.photo.pk)

    def test_parent_automatic_cover_uses_photos_from_deep_descendants(self):
        self.photo.captured_at += timedelta(days=1)
        self.photo.save()
        self.assertFalse(self.group.media_items.exists())
        self.assertEqual(self.effective_cover(self.group), self.photo.pk)
        self.assertEqual(self.effective_cover(self.branch), self.photo.pk)

    def test_manual_cover_overrides_newer_photos_and_can_return_to_automatic(self):
        self.multi.captured_at += timedelta(days=5)
        self.multi.save()
        self.choose(self.group, self.photo)
        self.assertEqual(self.effective_cover(self.group), self.photo.pk)
        self.assertTrue(self.group.cover_is_custom)
        self.choose(self.group, None)
        self.assertEqual(self.effective_cover(self.group), self.multi.pk)
        self.assertFalse(self.group.cover_is_custom)

    def test_private_cover_falls_back_per_viewer_without_revealing_the_hidden_branch(self):
        self.choose(self.group, self.hidden)
        self.assertEqual(self.effective_cover(self.group), self.hidden.pk)
        # Even if the photo is readable elsewhere, a hidden branch must not be revealed.
        AlbumMedia.objects.create(album=self.outside, media=self.hidden)
        self.assertTrue(MediaItem.objects.visible_to(self.viewer).filter(pk=self.hidden.pk).exists())
        self.assertIn(self.effective_cover(self.group, self.viewer), {self.photo.pk, self.multi.pk})
        self.assertFalse(self.group.cover_is_custom)
        self.assertEqual(self.effective_cover(self.group, self.guest), self.shared.pk)
        self.assertIsNone(self.effective_cover(self.group, self.outsider))

    def test_hidden_intermediate_album_stops_cover_traversal(self):
        self.choose(self.group, self.photo)
        self.branch.visibility = "private"
        self.branch.save()
        self.assertIsNone(self.effective_cover(self.group, self.viewer))
        self.assertFalse(AlbumCovers(self.viewer).photos_for(self.group.pk).exists())
        self.assertIn(self.effective_cover(self.family, self.viewer), {self.photo.pk, self.multi.pk})

    def test_moving_the_selected_branch_out_restores_an_automatic_cover(self):
        self.choose(self.group, self.photo)
        self.branch.parent = self.outside
        self.branch.save()
        self.assertNotEqual(self.effective_cover(self.group), self.photo.pk)
        self.assertFalse(self.group.cover_is_custom)

    def test_removing_the_selected_photo_membership_restores_automatic_cover(self):
        self.choose(self.group, self.photo)
        AlbumMedia.objects.create(album=self.outside, media=self.photo)
        AlbumMedia.objects.filter(album=self.family, media=self.photo).delete()
        self.assertNotEqual(self.effective_cover(self.group), self.photo.pk)

    def test_unready_or_missing_thumbnail_never_remains_a_displayed_cover(self):
        self.choose(self.group, self.photo)
        for changes in ({"status": "pending"}, {"status": "error"}, {"status": "ready", "thumbnail_key": ""}):
            with self.subTest(changes=changes):
                MediaItem.objects.filter(pk=self.photo.pk).update(**changes)
                self.assertNotEqual(self.effective_cover(self.group), self.photo.pk)

    def test_deleting_a_cover_photo_preserves_album_and_clears_the_reference(self):
        self.choose(self.group, self.photo)
        self.photo.delete()
        self.group.refresh_from_db()
        self.assertIsNone(self.group.cover_photo_id)
        self.assertIsNotNone(self.effective_cover(self.group))

    def test_custom_child_cover_does_not_override_parent_automatic_choice(self):
        self.multi.captured_at += timedelta(days=2)
        self.multi.save()
        self.choose(self.family, self.photo)
        self.assertEqual(self.effective_cover(self.family), self.photo.pk)
        self.assertEqual(self.effective_cover(self.group), self.multi.pk)


class AlbumCoverFormTests(CoverFixtures):
    def test_choices_are_scoped_to_ready_photos_with_thumbnails_in_the_branch(self):
        form = AlbumCoverForm(user=self.owner, album=self.group)
        ids = set(form.fields["cover_photo"].queryset.values_list("pk", flat=True))
        self.assertIn(self.photo.pk, ids)
        self.assertIn(self.hidden.pk, ids)
        for rejected in (self.outside_photo, self.pending, self.failed):
            self.assertNotIn(rejected.pk, ids)
            posted = AlbumCoverForm({"cover_photo": rejected.pk}, user=self.owner, album=self.group)
            self.assertFalse(posted.is_valid())

    def test_malformed_and_nonexistent_photo_ids_are_rejected(self):
        for value in ("not-a-uuid", uuid.uuid4()):
            with self.subTest(value=value):
                form = AlbumCoverForm({"cover_photo": value}, user=self.owner, album=self.group)
                self.assertFalse(form.is_valid())
                self.assertIn("cover_photo", form.errors)

    def test_a_photo_in_multiple_descendant_albums_appears_once(self):
        AlbumMedia.objects.create(album=self.group, media=self.photo)
        AlbumMedia.objects.create(album=self.branch, media=self.photo)
        photos = AlbumCovers(self.owner).photos_for(self.group.pk)
        self.assertEqual(photos.filter(pk=self.photo.pk).count(), 1)

    def test_management_permission_is_required_and_rechecked_at_save(self):
        for user in (self.viewer, self.guest, self.outsider):
            with self.subTest(user=user), self.assertRaises(PermissionDenied):
                AlbumCoverForm(user=user, album=self.group)
        form = AlbumCoverForm({"cover_photo": self.photo.pk}, user=self.owner, album=self.group)
        self.assertTrue(form.is_valid(), form.errors)
        Album.objects.filter(pk=self.group.pk).update(owner=self.viewer)
        with self.assertRaises(PermissionDenied):
            form.save()
        self.group.refresh_from_db()
        self.assertIsNone(self.group.cover_photo_id)

    def test_moving_a_branch_after_validation_rejects_stale_selection(self):
        form = AlbumCoverForm({"cover_photo": self.photo.pk}, user=self.owner, album=self.group)
        self.assertTrue(form.is_valid(), form.errors)
        self.branch.parent = None
        self.branch.save()
        with self.assertRaises(ValidationError):
            form.save()
        self.group.refresh_from_db()
        self.assertIsNone(self.group.cover_photo_id)

    def test_revoked_photo_access_after_validation_rejects_selection(self):
        child = Album.objects.create(owner=self.viewer, title="Zoo partagé", parent=self.group)
        photo = self.make_photo("Animal partagé", child, uploader=self.viewer)
        form = AlbumCoverForm({"cover_photo": photo.pk}, user=self.owner, album=self.group)
        self.assertTrue(form.is_valid(), form.errors)
        child.visibility = "private"
        child.save()
        with self.assertRaises(ValidationError):
            form.save()

    def test_admin_can_choose_cover_without_changing_album_owner_or_metadata(self):
        self.choose(self.group, self.photo, user=self.admin)
        self.group.refresh_from_db()
        self.assertEqual(self.group.owner_id, self.owner.pk)
        self.assertEqual(self.group.title, "Les zoos")
        self.assertEqual(self.group.visibility, "family")
        self.assertEqual(self.group.permissions.get().user_id, self.guest.pk)

    def test_stale_album_edit_cannot_overwrite_a_new_cover_choice(self):
        form = AlbumForm({
            "title": "Zoos du monde", "description": "", "parent": "", "visibility": "family",
            "allowed_users": [self.guest.pk], "cover_photo": self.outside_photo.pk,
        }, user=self.owner, instance=self.group)
        self.assertTrue(form.is_valid(), form.errors)
        self.choose(self.group, self.photo)
        form.save()
        self.group.refresh_from_db()
        self.assertEqual(self.group.cover_photo_id, self.photo.pk)


class AlbumCoverViewTests(CoverFixtures):
    def test_picker_is_read_only_on_get_and_selects_or_resets_on_post(self):
        self.client.force_login(self.owner)
        url = reverse("library:album_cover", args=[self.group.pk])
        response = self.client.get(url)
        self.assertContains(response, "Choisir la vignette")
        self.assertNotContains(response, self.outside_photo.title)
        self.group.refresh_from_db()
        self.assertIsNone(self.group.cover_photo_id)
        self.assertRedirects(self.client.post(url, {"cover_photo": self.photo.pk}), reverse("library:album_detail", args=[self.group.pk]))
        response = self.client.get(url)
        self.assertContains(response, "Vignette personnalisée")
        self.assertContains(response, "Revenir au choix automatique")
        self.assertEqual(self.client.post(url, {"cover_photo": ""}).status_code, 302)
        self.group.refresh_from_db()
        self.assertIsNone(self.group.cover_photo_id)

    def test_endpoint_requires_authentication_management_and_csrf(self):
        url = reverse("library:album_cover", args=[self.group.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        for user, expected in ((self.viewer, 403), (self.guest, 403), (self.outsider, 404)):
            self.client.force_login(user)
            for method in (self.client.get, self.client.post):
                with self.subTest(user=user, method=method):
                    self.assertEqual(method(url, {"cover_photo": self.photo.pk}).status_code, expected)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        self.assertEqual(client.post(url, {"cover_photo": self.photo.pk}).status_code, 403)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.delete(url).status_code, 405)

    def test_invalid_selection_returns_an_error_without_clearing_existing_cover(self):
        self.choose(self.group, self.photo)
        self.client.force_login(self.owner)
        response = self.client.post(reverse("library:album_cover", args=[self.group.pk]), {"cover_photo": self.outside_photo.pk})
        self.assertEqual(response.status_code, 200)
        self.assertIn("cover_photo", response.context["form"].errors)
        self.group.refresh_from_db()
        self.assertEqual(self.group.cover_photo_id, self.photo.pk)

    def test_empty_album_shows_a_useful_empty_state(self):
        empty = Album.objects.create(owner=self.owner, title="Sans photo")
        self.client.force_login(self.owner)
        response = self.client.get(reverse("library:album_cover", args=[empty.pk]))
        self.assertContains(response, "Aucune photo disponible pour la vignette")
        self.assertEqual(response.context["page_obj"].paginator.count, 0)

    def test_picker_search_and_pagination_remain_scoped_and_bounded(self):
        album = Album.objects.create(owner=self.owner, title="Nombreuses photos")
        photos = MediaItem.objects.bulk_create([
            MediaItem(uploader=self.owner, title=f"Animal {index}", original_name=f"animal-{index}.jpg", status="ready", thumbnail_key="test.webp")
            for index in range(61)
        ])
        AlbumMedia.objects.bulk_create([AlbumMedia(album=album, media=photo) for photo in photos])
        self.client.force_login(self.owner)
        url = reverse("library:album_cover", args=[album.pk])
        first = self.client.get(url, {"q": "Animal"})
        second = self.client.get(url, {"q": "Animal", "page": 2})
        self.assertEqual(first.context["page_obj"].paginator.count, 61)
        self.assertEqual(len(first.context["page_obj"]), 48)
        self.assertEqual(len(second.context["page_obj"]), 13)
        self.assertContains(first, '?q=Animal&amp;page=2')
        photo = second.context["page_obj"][0]
        self.assertEqual(self.client.post(url + "?q=Animal&page=2", {"cover_photo": photo.pk}).status_code, 302)
        self.assertEqual(self.effective_cover(album), photo.pk)
        self.assertEqual(self.client.get(url, {"q": self.outside_photo.title}).context["page_obj"].paginator.count, 0)

    def test_picker_escapes_photo_names_and_search_text(self):
        self.photo.title = '<script>alert("photo")</script>'
        self.photo.save()
        self.client.force_login(self.owner)
        response = self.client.get(reverse("library:album_cover", args=[self.group.pk]), {"q": "<script>"})
        self.assertNotContains(response, '<script>alert("photo")</script>')
        self.assertContains(response, "&lt;script&gt;")

    def test_root_cards_and_nested_cards_display_the_saved_cover(self):
        self.choose(self.group, self.photo)
        self.choose(self.branch, self.multi)
        self.client.force_login(self.owner)
        response = self.client.get(reverse("library:album_list"))
        card = next(album for album in response.context["page_obj"] if album.pk == self.group.pk)
        self.assertEqual(card.cover_id, self.photo.pk)
        response = self.client.get(reverse("library:album_detail", args=[self.group.pk]))
        card = next(album for album in response.context["subalbums_page"] if album.pk == self.branch.pk)
        self.assertEqual(card.cover_id, self.multi.pk)
        self.assertContains(response, reverse("library:album_cover", args=[self.group.pk]))

    def test_cards_compute_many_custom_descendant_covers_without_per_album_queries(self):
        for index in range(25):
            root = Album.objects.create(owner=self.owner, title=f"Groupe {index}", cover_photo=self.photo)
            child = Album.objects.create(owner=self.owner, title=f"Visite {index}", parent=root)
            AlbumMedia.objects.create(album=child, media=self.photo)
        self.client.force_login(self.owner)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("library:album_list"))
        self.assertEqual(len(response.context["page_obj"]), 24)
        self.assertTrue(all(album.cover_id == self.photo.pk for album in response.context["page_obj"]))
        self.assertLessEqual(len(queries), 8)

    @override_settings(FORCE_SCRIPT_NAME="/ma-pixelotheque")
    def test_cover_links_and_redirects_honor_the_deployment_prefix(self):
        self.client.force_login(self.owner)
        previous = get_script_prefix()
        try:
            set_script_prefix("/")
            path = reverse("library:album_cover", args=[self.group.pk])
            set_script_prefix("/ma-pixelotheque/")
            response = self.client.get(path, SCRIPT_NAME="/ma-pixelotheque")
            self.assertContains(response, reverse("library:media_file", args=[self.photo.pk, "thumbnail"]))
            response = self.client.post(path, {"cover_photo": self.photo.pk}, SCRIPT_NAME="/ma-pixelotheque")
            self.assertEqual(response.url, reverse("library:album_detail", args=[self.group.pk]))
        finally:
            set_script_prefix(previous)
