import uuid

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import get_script_prefix, reverse, set_script_prefix

from apps.library.forms import AlbumForm
from apps.library.models import Album, AlbumPermission
from apps.library.permissions import (
    can_create_album,
    can_manage_album,
    can_upload_to_album,
)


class AlbumFixtures(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.admin_user = users.create_superuser(username="admin", password=None)
        cls.owner = users.create_user(username="owner", role="family")
        cls.family = users.create_user(username="family", role="family")
        cls.member = users.create_user(username="member", role="family")
        cls.guest = users.create_user(username="guest", role="guest")
        cls.invited = users.create_user(username="invited", role="guest")
        cls.inactive = users.create_user(username="inactive", role="family", is_active=False)
        cls.staff = users.create_user(username="staff", role="family", is_staff=True)
        cls.albums = {
            "family": Album.objects.create(owner=cls.owner, title="Familial"),
            "locked": Album.objects.create(
                owner=cls.owner, title="Familial sans contribution", allow_family_uploads=False
            ),
            "restricted": Album.objects.create(
                owner=cls.owner, title="Restreint", visibility=Album.Visibility.RESTRICTED
            ),
            "private": Album.objects.create(
                owner=cls.owner, title="Privé", visibility=Album.Visibility.PRIVATE
            ),
        }
        for name in ("family", "locked", "restricted"):
            AlbumPermission.objects.create(album=cls.albums[name], user=cls.invited)
        AlbumPermission.objects.create(
            album=cls.albums["restricted"], user=cls.member, can_upload=True
        )

    def album_data(self, **changes):
        data = {
            "title": "Vacances en famille",
            "description": "De beaux souvenirs.",
            "visibility": "family",
            "allow_family_uploads": "on",
            "allowed_users": [],
            "contributing_users": [],
        }
        data.update(changes)
        return data


class AlbumPermissionTests(AlbumFixtures):
    def test_complete_visibility_matrix(self):
        cases = (
            (self.admin_user, {"family", "locked", "restricted", "private"}),
            (self.owner, {"family", "locked", "restricted", "private"}),
            (self.family, {"family", "locked"}),
            (self.member, {"family", "locked", "restricted"}),
            (self.guest, set()),
            (self.invited, {"family", "locked", "restricted"}),
            (self.inactive, set()),
            (self.staff, {"family", "locked"}),
            (AnonymousUser(), set()),
        )
        for user, expected in cases:
            with self.subTest(user=str(user)):
                visible = set(Album.objects.visible_to(user).values_list("pk", flat=True))
                self.assertEqual(visible, {self.albums[name].pk for name in expected})

    def test_complete_management_and_upload_matrix(self):
        cases = (
            (self.admin_user, {"family", "locked", "restricted", "private"}, {"family", "locked", "restricted", "private"}),
            (self.owner, {"family", "locked", "restricted", "private"}, {"family", "locked", "restricted", "private"}),
            (self.family, set(), {"family"}),
            (self.member, set(), {"family", "restricted"}),
            (self.guest, set(), set()),
            (self.invited, set(), set()),
            (self.inactive, set(), set()),
            (self.staff, set(), {"family"}),
            (AnonymousUser(), set(), set()),
        )
        for user, manageable, uploadable in cases:
            for name, album in self.albums.items():
                with self.subTest(user=str(user), album=name):
                    self.assertEqual(can_manage_album(user, album), name in manageable)
                    self.assertEqual(can_upload_to_album(user, album), name in uploadable)
                    self.assertEqual(album.can_manage(user), name in manageable)
                    self.assertEqual(album.can_upload(user), name in uploadable)

    def test_creation_is_limited_to_active_family_and_admin(self):
        for user, expected in (
            (self.admin_user, True), (self.owner, True), (self.family, True),
            (self.guest, False), (self.inactive, False), (AnonymousUser(), False),
        ):
            with self.subTest(user=str(user)):
                self.assertEqual(can_create_album(user), expected)

    def test_disabled_admin_cannot_read_or_mutate(self):
        self.admin_user.is_active = False
        self.assertFalse(Album.objects.visible_to(self.admin_user).exists())
        self.assertFalse(can_create_album(self.admin_user))
        self.assertFalse(can_manage_album(self.admin_user, self.albums["private"]))
        self.assertFalse(can_upload_to_album(self.admin_user, self.albums["private"]))

    def test_demoted_owner_keeps_read_access_without_management_or_upload(self):
        self.owner.role = "guest"
        self.owner.save(update_fields=["role"])
        self.assertEqual(Album.objects.visible_to(self.owner).count(), 4)
        self.assertFalse(self.albums["private"].can_manage(self.owner))
        self.assertFalse(self.albums["private"].can_upload(self.owner))

    def test_private_visibility_overrides_stale_permission_records(self):
        AlbumPermission.objects.create(album=self.albums["private"], user=self.member, can_upload=True)
        self.assertFalse(Album.objects.visible_to(self.member).filter(pk=self.albums["private"].pk).exists())
        self.assertFalse(self.albums["private"].can_upload(self.member))

    def test_member_demotion_removes_upload_right_even_if_flag_remains(self):
        self.member.role = "guest"
        self.member.save(update_fields=["role"])
        self.assertTrue(Album.objects.visible_to(self.member).filter(pk=self.albums["restricted"].pk).exists())
        self.assertFalse(self.albums["restricted"].can_upload(self.member))

    def test_visibility_has_no_duplicate_rows_when_explicit_member_is_family(self):
        AlbumPermission.objects.create(album=self.albums["family"], user=self.family)
        self.assertEqual(Album.objects.visible_to(self.family).count(), 2)

    def test_restricted_read_member_cannot_upload(self):
        AlbumPermission.objects.create(album=self.albums["restricted"], user=self.family)
        self.assertTrue(Album.objects.visible_to(self.family).filter(pk=self.albums["restricted"].pk).exists())
        self.assertFalse(self.albums["restricted"].can_upload(self.family))

    def test_album_permission_pair_is_unique_in_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            AlbumPermission.objects.create(album=self.albums["restricted"], user=self.member)

    def test_invalid_memberships_are_rejected_by_model_validation(self):
        cases = (
            {"album": self.albums["private"], "user": self.family},
            {"album": self.albums["family"], "user": self.owner},
            {"album": self.albums["family"], "user": self.inactive},
            {"album": self.albums["family"], "user": self.guest, "can_upload": True},
        )
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                AlbumPermission(**values).full_clean()


class AlbumFormTests(AlbumFixtures):
    def test_restricted_album_saves_members_and_contribution_permissions(self):
        form = AlbumForm(
            self.album_data(
                visibility="restricted",
                allowed_users=[self.member.pk, self.invited.pk],
                contributing_users=[self.member.pk],
            ),
            user=self.owner,
        )
        self.assertTrue(form.is_valid(), form.errors)
        album = form.save()
        self.assertEqual(album.owner, self.owner)
        self.assertEqual(
            set(album.permissions.values_list("user_id", "can_upload")),
            {(self.member.pk, True), (self.invited.pk, False)},
        )

    def test_unlisted_contributor_is_rejected(self):
        form = AlbumForm(
            self.album_data(visibility="restricted", contributing_users=[self.member.pk]),
            user=self.owner,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("contributing_users", form.errors)

    def test_inactive_owner_guest_contributor_and_unknown_users_are_rejected(self):
        for field, user_id in (
            ("allowed_users", self.inactive.pk),
            ("allowed_users", self.owner.pk),
            ("allowed_users", 9999999),
            ("contributing_users", self.guest.pk),
        ):
            with self.subTest(field=field, user=user_id):
                form = AlbumForm(self.album_data(**{field: [user_id]}), user=self.owner)
                self.assertFalse(form.is_valid())
                self.assertIn(field, form.errors)

    def test_private_album_rejects_explicit_members(self):
        form = AlbumForm(
            self.album_data(visibility="private", allowed_users=[self.invited.pk]), user=self.owner
        )
        self.assertFalse(form.is_valid())
        self.assertIn("allowed_users", form.errors)

    def test_changing_to_private_removes_previous_members(self):
        form = AlbumForm(
            self.album_data(visibility="private"),
            user=self.owner,
            instance=self.albums["restricted"],
        )
        self.assertTrue(form.is_valid(), form.errors)
        album = form.save()
        self.assertFalse(album.permissions.exists())
        self.assertFalse(Album.objects.visible_to(self.member).filter(pk=album.pk).exists())
        self.assertFalse(Album.objects.visible_to(self.invited).filter(pk=album.pk).exists())

    def test_edit_can_revoke_members_and_contribution_without_deleting_album(self):
        form = AlbumForm(
            self.album_data(visibility="restricted", allowed_users=[self.member.pk]),
            user=self.owner,
            instance=self.albums["restricted"],
        )
        self.assertTrue(form.is_valid(), form.errors)
        album = form.save()
        self.assertEqual(list(album.permissions.values_list("user_id", "can_upload")), [(self.member.pk, False)])
        self.assertFalse(Album.objects.visible_to(self.invited).filter(pk=album.pk).exists())

    def test_choices_expose_only_usernames_of_active_accounts(self):
        self.invited.email = "private-address@example.test"
        self.invited.first_name = "Hidden personal name"
        self.invited.save(update_fields=["email", "first_name"])
        form = AlbumForm(user=self.owner)
        rendered = str(form["allowed_users"])
        self.assertIn("invited", rendered)
        self.assertNotIn(self.invited.email, rendered)
        self.assertNotIn(self.invited.first_name, rendered)
        self.assertNotIn("inactive", rendered)
        self.assertNotIn(f'value="{self.owner.pk}"', rendered)

    def test_form_cannot_be_used_to_bypass_management_permissions(self):
        for user, instance in (
            (self.guest, None), (self.inactive, None), (self.family, self.albums["family"])
        ):
            with self.subTest(user=str(user)), self.assertRaises(PermissionDenied):
                AlbumForm(user=user, instance=instance)

    def test_owner_cannot_be_changed_by_posted_field(self):
        form = AlbumForm(
            self.album_data(owner=self.family.pk),
            user=self.owner,
            instance=self.albums["family"],
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().owner, self.owner)

    def test_save_rechecks_management_right_after_validation(self):
        form = AlbumForm(
            self.album_data(), user=self.owner, instance=self.albums["family"]
        )
        self.assertTrue(form.is_valid(), form.errors)
        Album.objects.filter(pk=self.albums["family"].pk).update(owner=self.family)
        with self.assertRaises(PermissionDenied):
            form.save()
        self.albums["family"].refresh_from_db()
        self.assertEqual(self.albums["family"].title, "Familial")

    def test_repeated_member_ids_create_only_one_permission(self):
        form = AlbumForm(
            self.album_data(allowed_users=[self.invited.pk, self.invited.pk]),
            user=self.owner,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().permissions.count(), 1)

    def test_maximum_title_and_description_lengths_are_enforced(self):
        form = AlbumForm(self.album_data(title="x" * 151, description="x" * 2001), user=self.owner)
        self.assertFalse(form.is_valid())
        self.assertIn("title", form.errors)
        self.assertIn("description", form.errors)


class AlbumViewTests(AlbumFixtures):
    def test_anonymous_requests_require_login(self):
        for route, args in (
            ("album_list", ()), ("album_create", ()),
            ("album_detail", (self.albums["private"].pk,)),
            ("album_edit", (self.albums["private"].pk,)),
        ):
            with self.subTest(route=route):
                response = self.client.get(reverse(f"library:{route}", args=args))
                self.assertEqual(response.status_code, 302)
                self.assertIn("next=", response.url)

    def test_guest_list_contains_only_explicitly_authorized_albums(self):
        self.client.force_login(self.invited)
        response = self.client.get(reverse("library:album_list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {album.pk for album in response.context["page_obj"]},
            {self.albums[name].pk for name in ("family", "locked", "restricted")},
        )
        self.assertNotContains(response, reverse("library:album_create"))
        self.assertNotContains(response, self.albums["private"].title)

    def test_album_detail_enforces_visibility_matrix(self):
        cases = (
            (self.admin_user, {"family", "locked", "restricted", "private"}),
            (self.owner, {"family", "locked", "restricted", "private"}),
            (self.family, {"family", "locked"}),
            (self.member, {"family", "locked", "restricted"}),
            (self.guest, set()),
            (self.invited, {"family", "locked", "restricted"}),
        )
        for user, visible in cases:
            self.client.force_login(user)
            for name, album in self.albums.items():
                with self.subTest(user=user, album=name):
                    response = self.client.get(reverse("library:album_detail", args=[album.pk]))
                    self.assertEqual(response.status_code, 200 if name in visible else 404)

    def test_uninvited_guest_has_empty_album_list(self):
        self.client.force_login(self.guest)
        response = self.client.get(reverse("library:album_list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].paginator.count, 0)

    def test_hidden_and_nonexistent_albums_both_return_404(self):
        self.client.force_login(self.family)
        for album_id in (self.albums["private"].pk, self.albums["restricted"].pk, uuid.uuid4()):
            for route in ("album_detail", "album_edit"):
                with self.subTest(route=route, album=album_id):
                    response = self.client.get(reverse(f"library:{route}", args=[album_id]))
                    self.assertEqual(response.status_code, 404)

    def test_reader_cannot_access_or_post_to_management_form(self):
        self.client.force_login(self.family)
        url = reverse("library:album_edit", args=[self.albums["family"].pk])
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, self.album_data()).status_code, 403)
        self.albums["family"].refresh_from_db()
        self.assertEqual(self.albums["family"].title, "Familial")

    def test_guest_cannot_create_album_or_list_invitation_candidates(self):
        self.client.force_login(self.guest)
        url = reverse("library:album_create")
        for method in (self.client.get, self.client.post):
            response = method(url, self.album_data())
            self.assertEqual(response.status_code, 403)
            self.assertNotContains(response, "invited", status_code=403)
        self.assertEqual(Album.objects.count(), 4)

    def test_family_create_uses_request_user_and_ignores_owner_override(self):
        self.client.force_login(self.family)
        response = self.client.post(
            reverse("library:album_create"), self.album_data(owner=self.admin_user.pk)
        )
        album = Album.objects.get(title="Vacances en famille")
        self.assertEqual(album.owner, self.family)
        self.assertRedirects(response, reverse("library:album_detail", args=[album.pk]))

    def test_admin_can_edit_another_users_private_album_without_changing_owner(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("library:album_edit", args=[self.albums["private"].pk]),
            self.album_data(visibility="private", owner=self.admin_user.pk),
        )
        self.assertEqual(response.status_code, 302)
        self.albums["private"].refresh_from_db()
        self.assertEqual(self.albums["private"].owner, self.owner)
        self.assertEqual(self.albums["private"].title, "Vacances en famille")

    def test_csrf_is_required_for_creation_and_editing(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        for route, args in (("album_create", ()), ("album_edit", (self.albums["family"].pk,))):
            with self.subTest(route=route):
                response = client.post(reverse(f"library:{route}", args=args), self.album_data())
                self.assertEqual(response.status_code, 403)
        self.assertEqual(Album.objects.count(), 4)

    def test_get_forms_do_not_mutate_albums(self):
        self.client.force_login(self.owner)
        for route, args in (("album_create", ()), ("album_edit", (self.albums["family"].pk,))):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(reverse(f"library:{route}", args=args)).status_code, 200)
        self.assertEqual(Album.objects.count(), 4)
        self.albums["family"].refresh_from_db()
        self.assertEqual(self.albums["family"].title, "Familial")

    def test_non_supported_mutation_methods_are_rejected(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.delete(reverse("library:album_edit", args=[self.albums["family"].pk])).status_code, 405)
        self.assertEqual(self.client.post(reverse("library:album_list")).status_code, 405)
        self.assertEqual(self.client.post(reverse("library:album_detail", args=[self.albums["family"].pk])).status_code, 405)

    def test_album_list_paginates_after_permission_filtering(self):
        Album.objects.bulk_create([
            Album(owner=self.owner, title=f"Visible {index}") for index in range(25)
        ] + [
            Album(owner=self.owner, title=f"Secret {index}", visibility="private") for index in range(30)
        ])
        self.client.force_login(self.family)
        first = self.client.get(reverse("library:album_list"))
        second = self.client.get(reverse("library:album_list"), {"page": 2})
        self.assertEqual(first.context["page_obj"].paginator.count, 27)
        self.assertEqual(len(first.context["page_obj"]), 24)
        self.assertEqual(len(second.context["page_obj"]), 3)
        self.assertFalse(
            {album.pk for album in first.context["page_obj"]}
            & {album.pk for album in second.context["page_obj"]}
        )
        self.assertNotContains(first, "Secret")

    def test_titles_and_descriptions_are_escaped(self):
        album = self.albums["family"]
        album.title = '<script>alert("title")</script>'
        album.description = '<script>alert("description")</script>'
        album.save()
        self.client.force_login(self.family)
        response = self.client.get(reverse("library:album_detail", args=[album.pk]))
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, '<script>alert("title")</script>')
        self.assertNotContains(response, '<script>alert("description")</script>')

    @override_settings(FORCE_SCRIPT_NAME="/ma-pixelotheque")
    def test_album_links_and_redirects_honor_script_prefix(self):
        self.client.force_login(self.owner)
        previous_prefix = get_script_prefix()
        try:
            set_script_prefix("/")
            create_path = reverse("library:album_create")
            list_path = reverse("library:album_list")
            set_script_prefix("/ma-pixelotheque/")
            expected_list = reverse("library:album_list")
            response = self.client.get(create_path, SCRIPT_NAME="/ma-pixelotheque")
            self.assertContains(response, expected_list)
            response = self.client.post(
                create_path, self.album_data(), SCRIPT_NAME="/ma-pixelotheque"
            )
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.url.startswith("/ma-pixelotheque/"))
            self.assertEqual(self.client.get(list_path, SCRIPT_NAME="/ma-pixelotheque").status_code, 200)
        finally:
            set_script_prefix(previous_prefix)


class AlbumAdminTests(AlbumFixtures):
    def test_admin_models_require_superuser_even_for_staff_accounts(self):
        factory = RequestFactory()
        for model in (Album, AlbumPermission):
            model_admin = admin.site._registry[model]
            for user, expected in ((self.admin_user, True), (self.staff, False), (self.family, False), (self.guest, False)):
                request = factory.get("/admin/")
                request.user = user
                with self.subTest(model=model, user=user):
                    self.assertEqual(model_admin.has_module_permission(request), expected)
                    self.assertEqual(model_admin.has_view_permission(request), expected)
                    self.assertEqual(model_admin.has_add_permission(request), expected)
                    self.assertEqual(model_admin.has_change_permission(request), expected)
                    self.assertEqual(model_admin.has_delete_permission(request), expected)

    def test_owner_is_readonly_on_existing_album_in_admin(self):
        request = RequestFactory().get("/admin/")
        request.user = self.admin_user
        model_admin = admin.site._registry[Album]
        self.assertIn("owner", model_admin.get_readonly_fields(request, self.albums["family"]))
