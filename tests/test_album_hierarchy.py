import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import get_script_prefix, reverse, set_script_prefix

from apps.library.forms import AlbumForm, AlbumGroupingForm
from apps.library.models import Album, AlbumMedia, AlbumPermission, MediaItem
from apps.sharing.models import SharedLink
from tests.test_albums import AlbumFixtures


class HierarchyFixtures(AlbumFixtures):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.zoo = Album.objects.create(owner=cls.owner, title="Zoo")
        cls.beauval = Album.objects.create(owner=cls.owner, title="Zoo de Beauval", parent=cls.zoo)
        cls.berlin = Album.objects.create(owner=cls.owner, title="Zoo de Berlin", parent=cls.zoo)
        cls.pandas = Album.objects.create(owner=cls.owner, title="Les pandas", parent=cls.beauval)
        cls.photo = MediaItem.objects.create(
            uploader=cls.owner, title="Un panda", original_name="panda.jpg", status="ready",
            thumbnail_key="thumbnails/test.webp",
        )
        AlbumMedia.objects.create(album=cls.pandas, media=cls.photo)


class AlbumHierarchyModelTests(HierarchyFixtures):
    def test_model_rejects_self_and_descendant_as_parent(self):
        for parent in (self.zoo, self.beauval, self.pandas):
            with self.subTest(parent=parent.title):
                self.zoo.parent = parent
                with self.assertRaises(ValidationError):
                    self.zoo.full_clean()
                with self.assertRaises(ValidationError):
                    self.zoo.save()
        self.zoo.refresh_from_db()
        self.assertIsNone(self.zoo.parent_id)

    def test_database_rejects_self_parent_even_for_queryset_update(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Album.objects.filter(pk=self.zoo.pk).update(parent_id=self.zoo.pk)

    def test_deleting_parent_preserves_children_and_their_photos(self):
        self.zoo.delete()
        self.beauval.refresh_from_db()
        self.berlin.refresh_from_db()
        self.pandas.refresh_from_db()
        self.assertIsNone(self.beauval.parent_id)
        self.assertIsNone(self.berlin.parent_id)
        self.assertEqual(self.pandas.parent_id, self.beauval.pk)
        self.assertTrue(self.pandas.media_items.filter(pk=self.photo.pk).exists())

    def test_bulk_deletion_preserves_unselected_descendants(self):
        Album.objects.filter(pk__in=[self.zoo.pk, self.beauval.pk]).delete()
        self.pandas.refresh_from_db()
        self.assertIsNone(self.pandas.parent_id)
        self.assertTrue(self.pandas.media_items.filter(pk=self.photo.pk).exists())

    def test_parent_photos_still_block_unsafe_deletion(self):
        AlbumMedia.objects.create(album=self.zoo, media=MediaItem.objects.create(
            uploader=self.owner, original_name="lion.jpg",
        ))
        with self.assertRaises(ProtectedError):
            self.zoo.delete()
        self.beauval.refresh_from_db()
        self.assertEqual(self.beauval.parent_id, self.zoo.pk)


class AlbumHierarchyFormTests(HierarchyFixtures):
    def test_existing_album_can_move_with_its_subtree_and_return_to_root(self):
        form = AlbumForm(self.album_data(parent=self.berlin.pk), user=self.owner, instance=self.beauval)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.pandas.refresh_from_db()
        self.assertEqual(self.pandas.parent_id, self.beauval.pk)
        self.assertTrue(self.pandas.media_items.filter(pk=self.photo.pk).exists())
        form = AlbumForm(self.album_data(parent=""), user=self.owner, instance=self.beauval)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.save().parent_id)

    def test_parent_choices_exclude_self_descendants_and_other_owners(self):
        other = Album.objects.create(owner=self.family, title="Autre propriétaire")
        form = AlbumForm(user=self.owner, instance=self.beauval)
        choices = set(form.fields["parent"].queryset.values_list("pk", flat=True))
        self.assertIn(self.zoo.pk, choices)
        self.assertIn(self.berlin.pk, choices)
        for excluded in (self.beauval, self.pandas, other):
            self.assertNotIn(excluded.pk, choices)
            posted = AlbumForm(self.album_data(parent=excluded.pk), user=self.owner, instance=self.beauval)
            self.assertFalse(posted.is_valid())
            self.assertIn("parent", posted.errors)

    def test_admin_can_choose_a_parent_owned_by_someone_else(self):
        target = Album.objects.create(owner=self.family, title="Tous les zoos", visibility="private")
        form = AlbumForm(self.album_data(parent=target.pk), user=self.admin_user, instance=self.zoo)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().parent_id, target.pk)

    def test_unmanageable_parent_can_be_kept_without_exposing_it_or_removed(self):
        target = Album.objects.create(owner=self.family, title="Dossier confidentiel", visibility="private")
        self.zoo.parent = target
        self.zoo.save()
        form = AlbumForm(user=self.owner, instance=self.zoo)
        self.assertNotIn(target.title, str(form))
        self.assertNotIn(str(target.pk), str(form))
        self.assertEqual(form.initial["parent"], "__keep__")
        form = AlbumForm(self.album_data(parent="__keep__"), user=self.owner, instance=self.zoo)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().parent_id, target.pk)
        form = AlbumForm(self.album_data(parent=""), user=self.owner, instance=self.zoo)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.save().parent_id)

    def test_keep_marker_cannot_be_used_for_a_new_album(self):
        form = AlbumForm(self.album_data(parent="__keep__"), user=self.owner)
        self.assertFalse(form.is_valid())
        self.assertIn("parent", form.errors)

    def test_destination_permissions_are_rechecked_when_saving(self):
        form = AlbumForm(self.album_data(parent=self.berlin.pk), user=self.owner, instance=self.beauval)
        self.assertTrue(form.is_valid(), form.errors)
        Album.objects.filter(pk=self.berlin.pk).update(owner=self.family)
        with self.assertRaises(PermissionDenied):
            form.save()
        self.beauval.refresh_from_db()
        self.assertEqual(self.beauval.parent_id, self.zoo.pk)

    def test_cycle_created_after_form_validation_is_rejected_on_save(self):
        form = AlbumForm(self.album_data(parent=self.berlin.pk), user=self.owner, instance=self.beauval)
        self.assertTrue(form.is_valid(), form.errors)
        self.berlin.parent = self.pandas
        self.berlin.save()
        with self.assertRaises(ValidationError):
            form.save()
        self.beauval.refresh_from_db()
        self.assertEqual(self.beauval.parent_id, self.zoo.pk)

    def test_grouping_moves_existing_albums_without_changing_their_permissions(self):
        form = AlbumGroupingForm(
            {"albums": [self.albums["private"].pk, self.albums["restricted"].pk]},
            user=self.owner, album=self.zoo,
        )
        members = list(self.albums["restricted"].permissions.values_list("user_id", "can_upload"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save(), 2)
        for name in ("private", "restricted"):
            self.albums[name].refresh_from_db()
            self.assertEqual(self.albums[name].parent_id, self.zoo.pk)
            self.assertEqual(self.albums[name].visibility, name)
        self.assertEqual(list(self.albums["restricted"].permissions.values_list("user_id", "can_upload")), members)

    def test_grouping_rejects_ancestors_self_and_unmanageable_albums(self):
        other = Album.objects.create(owner=self.family, title="Autre album familial")
        for album in (self.zoo, self.beauval, self.pandas, other):
            form = AlbumGroupingForm({"albums": [album.pk]}, user=self.owner, album=self.pandas)
            with self.subTest(album=album.title):
                self.assertFalse(form.is_valid())
                self.assertIn("albums", form.errors)

    def test_grouping_rechecks_both_source_and_destination_permissions(self):
        for changed in (self.zoo, self.albums["private"]):
            form = AlbumGroupingForm({"albums": [self.albums["private"].pk]}, user=self.owner, album=self.zoo)
            self.assertTrue(form.is_valid(), form.errors)
            Album.objects.filter(pk=changed.pk).update(owner=self.family)
            with self.subTest(changed=changed.title), self.assertRaises(PermissionDenied):
                form.save()
            Album.objects.filter(pk=changed.pk).update(owner=self.owner)
        self.albums["private"].refresh_from_db()
        self.assertIsNone(self.albums["private"].parent_id)

    def test_grouping_rolls_back_all_moves_if_hierarchy_changed(self):
        ancestor = Album.objects.create(owner=self.owner, title="Futur parent")
        sibling = Album.objects.create(owner=self.owner, title="Album à déplacer")
        form = AlbumGroupingForm({"albums": [ancestor.pk, sibling.pk]}, user=self.owner, album=self.zoo)
        self.assertTrue(form.is_valid(), form.errors)
        self.zoo.parent = ancestor
        self.zoo.save()
        with self.assertRaises(ValidationError):
            form.save()
        sibling.refresh_from_db()
        self.assertIsNone(sibling.parent_id)

    def test_grouping_an_album_and_its_descendant_keeps_the_branch_intact(self):
        form = AlbumGroupingForm(
            {"albums": [self.beauval.pk, self.pandas.pk]}, user=self.owner, album=self.berlin,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save(), 1)
        self.beauval.refresh_from_db()
        self.pandas.refresh_from_db()
        self.assertEqual(self.beauval.parent_id, self.berlin.pk)
        self.assertEqual(self.pandas.parent_id, self.beauval.pk)


class AlbumHierarchyViewTests(HierarchyFixtures):
    def test_root_shows_only_top_level_albums_with_authorized_child_counts(self):
        self.albums["private"].parent = self.zoo
        self.albums["private"].save()
        self.client.force_login(self.family)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("library:album_list"))
        cards = {album.pk: album for album in response.context["page_obj"]}
        self.assertIn(self.zoo.pk, cards)
        self.assertEqual(cards[self.zoo.pk].child_count, 2)
        self.assertNotIn(self.beauval.pk, cards)
        self.assertNotIn(self.pandas.pk, cards)
        self.assertNotContains(response, self.albums["private"].title)
        self.assertLessEqual(len(queries), 8)

    def test_detail_shows_direct_children_and_ordered_breadcrumbs(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("library:album_detail", args=[self.beauval.pk]))
        self.assertEqual([item.pk for item in response.context["ancestors"]], [self.zoo.pk])
        self.assertEqual([item.pk for item in response.context["subalbums_page"]], [self.pandas.pk])
        self.assertNotContains(response, "Aucune photo")
        response = self.client.get(reverse("library:album_detail", args=[self.pandas.pk]))
        self.assertEqual([item.pk for item in response.context["ancestors"]], [self.zoo.pk, self.beauval.pk])
        self.assertEqual([item.pk for item in response.context["media_items"]], [self.photo.pk])

    def test_inaccessible_parent_does_not_hide_authorized_child_or_leak_its_name(self):
        AlbumPermission.objects.create(album=self.beauval, user=self.guest)
        self.client.force_login(self.guest)
        response = self.client.get(reverse("library:album_list"))
        self.assertEqual([item.pk for item in response.context["page_obj"]], [self.beauval.pk])
        response = self.client.get(reverse("library:album_detail", args=[self.beauval.pk]))
        self.assertEqual(response.context["ancestors"], [])
        self.assertEqual(response.context["subalbums_page"].paginator.count, 0)
        self.assertNotContains(response, reverse("library:album_detail", args=[self.zoo.pk]))
        self.assertNotContains(response, self.pandas.title)
        self.assertNotContains(response, reverse("library:album_group", args=[self.beauval.pk]))
        self.assertEqual(self.client.get(reverse("library:album_detail", args=[self.zoo.pk])).status_code, 404)

    def test_breadcrumbs_stop_at_hidden_intermediate_album(self):
        self.beauval.visibility = "private"
        self.beauval.save()
        self.client.force_login(self.family)
        root = self.client.get(reverse("library:album_list"))
        self.assertIn(self.pandas.pk, [item.pk for item in root.context["page_obj"]])
        detail = self.client.get(reverse("library:album_detail", args=[self.pandas.pk]))
        self.assertEqual(detail.context["ancestors"], [])
        self.assertNotContains(detail, self.beauval.title)

    def test_parent_access_does_not_grant_private_child_or_photo_access(self):
        self.pandas.visibility = "private"
        self.pandas.save()
        self.client.force_login(self.family)
        response = self.client.get(reverse("library:album_detail", args=[self.beauval.pk]))
        self.assertNotContains(response, self.pandas.title)
        self.assertEqual(response.context["subalbums_page"].paginator.count, 0)
        self.assertFalse(MediaItem.objects.visible_to(self.family).filter(pk=self.photo.pk).exists())
        self.assertEqual(self.client.get(reverse("library:media_file", args=[self.photo.pk, "thumbnail"])).status_code, 404)

    def test_create_subalbum_prefills_parent_and_preserves_validation_errors(self):
        self.client.force_login(self.owner)
        url = reverse("library:album_create") + f"?parent={self.zoo.pk}"
        response = self.client.get(url)
        self.assertEqual(response.context["form"]["parent"].value(), self.zoo.pk)
        self.assertContains(response, "Créer un sous-album")
        response = self.client.post(url, self.album_data(title="", parent=self.zoo.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(str(response.context["form"]["parent"].value()), str(self.zoo.pk))
        response = self.client.post(url, self.album_data(title="Zoo de Tokyo", parent=self.zoo.pk))
        created = Album.objects.get(title="Zoo de Tokyo")
        self.assertEqual(created.parent_id, self.zoo.pk)
        self.assertRedirects(response, reverse("library:album_detail", args=[created.pk]))

    def test_invalid_hidden_and_unmanageable_creation_parents_are_rejected(self):
        self.client.force_login(self.family)
        for parent, status in (("invalid", 404), (uuid.uuid4(), 404), (self.albums["private"].pk, 404), (self.zoo.pk, 403)):
            with self.subTest(parent=parent):
                response = self.client.get(reverse("library:album_create"), {"parent": parent})
                self.assertEqual(response.status_code, status)

    def test_subalbums_and_photos_have_independent_permission_filtered_pagination(self):
        Album.objects.bulk_create([
            Album(owner=self.owner, title=f"Zoo visible {i}", parent=self.berlin) for i in range(25)
        ] + [
            Album(owner=self.owner, title=f"Zoo caché {i}", parent=self.berlin, visibility="private") for i in range(30)
        ])
        photos = MediaItem.objects.bulk_create([
            MediaItem(uploader=self.owner, original_name=f"photo-{i}.jpg", status="ready") for i in range(61)
        ])
        AlbumMedia.objects.bulk_create([AlbumMedia(album=self.berlin, media=photo) for photo in photos])
        self.client.force_login(self.family)
        response = self.client.get(reverse("library:album_detail", args=[self.berlin.pk]), {"page": 2, "albums_page": 2})
        self.assertEqual(response.context["subalbums_page"].paginator.count, 25)
        self.assertEqual(len(response.context["subalbums_page"]), 1)
        self.assertEqual(len(response.context["media_items"]), 1)
        self.assertContains(response, '?page=2&amp;albums_page=1')
        self.assertContains(response, '?albums_page=2&amp;page=1')
        self.assertNotContains(response, "Zoo caché")

    def test_grouping_endpoint_moves_several_albums_and_requires_management(self):
        url = reverse("library:album_group", args=[self.zoo.pk])
        data = {"albums": [self.albums["family"].pk, self.albums["locked"].pk]}
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.family)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, data).status_code, 403)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertIsNone(Album.objects.get(pk=self.albums["family"].pk).parent_id)
        self.assertEqual(self.client.delete(url).status_code, 405)
        self.assertRedirects(self.client.post(url, data), reverse("library:album_detail", args=[self.zoo.pk]))
        self.assertEqual(self.zoo.children.count(), 4)

    def test_grouping_endpoint_hides_private_albums_and_requires_csrf(self):
        self.client.force_login(self.family)
        self.assertEqual(self.client.get(reverse("library:album_group", args=[self.albums["private"].pk])).status_code, 404)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        response = client.post(reverse("library:album_group", args=[self.zoo.pk]), {"albums": [self.albums["family"].pk]})
        self.assertEqual(response.status_code, 403)

    def test_sharing_parent_does_not_implicitly_share_child_albums_or_photos(self):
        _, token = SharedLink.issue(album=self.zoo, creator=self.owner)
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("sharing:album", kwargs={"token": token}))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.beauval.title)
        self.assertNotContains(response, self.pandas.title)
        self.assertEqual(self.client.get(reverse("sharing:photo", kwargs={"token": token, "media_id": self.photo.pk})).status_code, 404)

    @override_settings(FORCE_SCRIPT_NAME="/ma-pixelotheque")
    def test_hierarchy_links_honor_script_prefix(self):
        self.client.force_login(self.owner)
        previous_prefix = get_script_prefix()
        try:
            set_script_prefix("/")
            path = reverse("library:album_detail", args=[self.beauval.pk])
            set_script_prefix("/ma-pixelotheque/")
            response = self.client.get(path, SCRIPT_NAME="/ma-pixelotheque")
            self.assertContains(response, reverse("library:album_detail", args=[self.zoo.pk]))
            self.assertContains(response, reverse("library:album_create") + f"?parent={self.beauval.pk}")
            self.assertContains(response, reverse("library:album_group", args=[self.beauval.pk]))
        finally:
            set_script_prefix(previous_prefix)
