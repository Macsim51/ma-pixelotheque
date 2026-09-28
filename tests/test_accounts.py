from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import authenticate, get_user_model
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, transaction
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.admin_site import SuperuserAdminSite
from apps.accounts.models import LoginRateLimit
from apps.accounts.rate_limits import release_login_attempt, reserve_login_attempt


User = get_user_model()
PASSWORD = "Photo-de-famille-7!Bleu"
BACKEND = "apps.accounts.backends.RateLimitedModelBackend"


@override_settings(AUTHENTICATION_BACKENDS=[BACKEND])
class AccountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.family = User.objects.create_user("famille", password=PASSWORD, role="family")
        cls.guest = User.objects.create_user("invite", password=PASSWORD)
        cls.admin_user = User.objects.create_superuser("admin", password=PASSWORD)

    def test_guest_is_default_and_superuser_requires_staff(self):
        self.assertEqual(self.guest.role, "guest")
        self.assertEqual(self.guest.theme, "system")
        self.assertFalse(self.guest.is_staff)
        self.assertTrue(self.admin_user.is_staff)
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.filter(pk=self.admin_user.pk).update(is_staff=False)

    def test_unknown_roles_are_rejected_by_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.filter(pk=self.guest.pk).update(role="admin")

    def test_admin_role_validation_explains_staff_requirement(self):
        self.admin_user.is_staff = False
        with self.assertRaises(ValidationError) as error:
            self.admin_user.clean()
        self.assertIn("is_staff", error.exception.message_dict)

    def test_family_and_guest_can_log_in(self):
        for user in (self.family, self.guest):
            with self.subTest(user=user.username):
                client = Client()
                response = client.post(reverse("accounts:login"), {"username": user.username, "password": PASSWORD})
                self.assertRedirects(response, reverse("library:album_list"), fetch_redirect_response=False)
                self.assertEqual(client.session["_auth_user_id"], str(user.pk))

    def test_inactive_account_cannot_authenticate(self):
        self.family.is_active = False
        self.family.save(update_fields=["is_active"])
        self.assertIsNone(authenticate(username=self.family.username, password=PASSWORD))

    def test_deactivation_invalidates_an_existing_session(self):
        self.client.force_login(self.family)
        User.objects.filter(pk=self.family.pk).update(is_active=False)
        response = self.client.get(reverse("accounts:account"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_external_next_cannot_redirect_off_site(self):
        for next_url in ("https://attacker.example/", "//attacker.example/", "http://testserver.evil.example/"):
            with self.subTest(next_url=next_url):
                response = Client().post(reverse("accounts:login"), {"username": self.family.username, "password": PASSWORD, "next": next_url})
                self.assertRedirects(response, reverse("library:album_list"), fetch_redirect_response=False)

    def test_safe_next_is_preserved(self):
        response = self.client.post(reverse("accounts:login"), {"username": self.family.username, "password": PASSWORD, "next": reverse("accounts:account")})
        self.assertRedirects(response, reverse("accounts:account"), fetch_redirect_response=False)

    def test_login_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(reverse("accounts:login"), {"username": self.family.username, "password": PASSWORD})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("_auth_user_id", client.session)

    def test_logout_is_post_only_and_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.family)
        self.assertEqual(client.get(reverse("accounts:logout")).status_code, 405)
        self.assertEqual(client.post(reverse("accounts:logout")).status_code, 403)
        response = client.get(reverse("accounts:account"))
        csrf = response.context["csrf_token"]
        response = client.post(reverse("accounts:logout"), {"csrfmiddlewaretoken": csrf})
        self.assertRedirects(response, reverse("accounts:login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", client.session)

    def test_account_requires_login(self):
        response = self.client.get(reverse("accounts:account"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_account_page_is_not_cached(self):
        self.client.force_login(self.family)
        response = self.client.get(reverse("accounts:account"))
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_preference_form_cannot_escalate_privileges(self):
        self.client.force_login(self.guest)
        response = self.client.post(reverse("accounts:account"), {"action": "preferences", "theme": "dark", "role": "family", "is_staff": "1", "is_superuser": "1"})
        self.assertRedirects(response, reverse("accounts:account"), fetch_redirect_response=False)
        self.guest.refresh_from_db()
        self.assertEqual(self.guest.theme, "dark")
        self.assertEqual(self.guest.role, "guest")
        self.assertFalse(self.guest.is_staff)
        self.assertFalse(self.guest.is_superuser)

    def test_invalid_theme_does_not_save(self):
        self.client.force_login(self.family)
        response = self.client.post(reverse("accounts:account"), {"action": "preferences", "theme": "<script>"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["preferences_form"].errors)
        self.family.refresh_from_db()
        self.assertEqual(self.family.theme, "system")

    def test_preferences_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.family)
        self.assertEqual(client.post(reverse("accounts:account"), {"action": "preferences", "theme": "dark"}).status_code, 403)
        self.family.refresh_from_db()
        self.assertEqual(self.family.theme, "system")

    def test_password_change_checks_old_password_and_keeps_current_session(self):
        self.client.force_login(self.family)
        new_password = "Nouveau-souvenir-84!Lumiere"
        payload = {"action": "password", "old_password": "incorrect", "new_password1": new_password, "new_password2": new_password}
        response = self.client.post(reverse("accounts:account"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertIn("old_password", response.context["password_form"].errors)
        payload["old_password"] = PASSWORD
        response = self.client.post(reverse("accounts:account"), payload)
        self.assertRedirects(response, reverse("accounts:account"), fetch_redirect_response=False)
        self.family.refresh_from_db()
        self.assertTrue(self.family.check_password(new_password))
        self.assertFalse(self.family.check_password(PASSWORD))
        self.assertEqual(self.client.get(reverse("accounts:account")).status_code, 200)

    @override_settings(AUTH_PASSWORD_VALIDATORS=[{"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}}])
    def test_password_change_runs_django_validators(self):
        self.client.force_login(self.family)
        response = self.client.post(reverse("accounts:account"), {"action": "password", "old_password": PASSWORD, "new_password1": "short", "new_password2": "short"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("new_password2", response.context["password_form"].errors)
        self.family.refresh_from_db()
        self.assertTrue(self.family.check_password(PASSWORD))

    def test_admin_site_is_restricted_to_active_superusers(self):
        self.assertIsInstance(admin.site, SuperuserAdminSite)
        factory = RequestFactory()
        request = factory.get("/admin/")
        for user in (self.family, self.guest):
            user.is_staff = True
            request.user = user
            self.assertFalse(admin.site.has_permission(request))
        request.user = self.admin_user
        self.assertTrue(admin.site.has_permission(request))
        self.admin_user.is_active = False
        self.assertFalse(admin.site.has_permission(request))

    def test_staff_non_superuser_cannot_log_into_admin(self):
        User.objects.filter(pk=self.family.pk).update(is_staff=True)
        response = self.client.post(reverse("admin:login"), {"username": self.family.username, "password": PASSWORD, "next": reverse("admin:index")})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)


@override_settings(
    AUTHENTICATION_BACKENDS=[BACKEND],
    LOGIN_RATE_LIMIT_WINDOW_SECONDS=900,
    LOGIN_RATE_LIMIT_ATTEMPTS=2,
    LOGIN_RATE_LIMIT_USERNAME_ATTEMPTS=4,
    LOGIN_RATE_LIMIT_IP_ATTEMPTS=8,
)
class LoginRateLimitTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("famille", password=PASSWORD, role="family")
        cls.admin_user = User.objects.create_superuser("admin", password=PASSWORD)

    def request(self, ip="192.0.2.1", **extra):
        return RequestFactory().post("/login/", REMOTE_ADDR=ip, **extra)

    def test_pair_budget_blocks_even_the_correct_password_after_failures(self):
        request = self.request()
        for _ in range(2):
            self.assertIsNone(authenticate(request, username=self.user.username, password="wrong"))
        self.assertIsNone(authenticate(request, username=self.user.username, password=PASSWORD))

    def test_normalized_usernames_share_a_budget(self):
        self.assertIsNone(authenticate(self.request(), username="FAMILLE", password="wrong"))
        self.assertIsNone(authenticate(self.request(), username="ｆａｍｉｌｌｅ", password="wrong"))
        self.assertIsNone(authenticate(self.request(), username="famille", password=PASSWORD))

    def test_success_does_not_clear_preceding_failures(self):
        request = self.request()
        self.assertIsNone(authenticate(request, username="famille", password="wrong"))
        self.assertEqual(authenticate(request, username="famille", password=PASSWORD), self.user)
        self.assertIsNone(authenticate(request, username="famille", password="wrong"))
        self.assertIsNone(authenticate(request, username="famille", password=PASSWORD))

    def test_account_budget_stops_attempts_from_different_addresses(self):
        for number in range(4):
            self.assertIsNotNone(reserve_login_attempt(self.request(f"192.0.2.{number + 1}"), "famille"))
        self.assertIsNone(reserve_login_attempt(self.request("198.51.100.1"), "famille"))

    def test_ip_budget_stops_attempts_against_new_account_names(self):
        for number in range(8):
            self.assertIsNotNone(reserve_login_attempt(self.request(), f"unknown-{number}"))
        self.assertIsNone(reserve_login_attempt(self.request(), "another-name"))

    def test_forwarded_header_does_not_bypass_the_limit(self):
        for number in range(2):
            request = self.request(HTTP_X_FORWARDED_FOR=f"198.51.100.{number + 1}")
            self.assertIsNotNone(reserve_login_attempt(request, "famille"))
        self.assertIsNone(reserve_login_attempt(self.request(HTTP_X_FORWARDED_FOR="203.0.113.1"), "famille"))

    def test_expired_window_allows_login_without_cleanup(self):
        request = self.request()
        reserve_login_attempt(request, "famille")
        reserve_login_attempt(request, "famille")
        LoginRateLimit.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(authenticate(request, username="famille", password=PASSWORD), self.user)

    def test_a_blocked_attempt_does_not_extend_expiry(self):
        request = self.request()
        reserve_login_attempt(request, "famille")
        reserve_login_attempt(request, "famille")
        expiry = dict(LoginRateLimit.objects.values_list("key", "expires_at"))
        self.assertIsNone(reserve_login_attempt(request, "famille"))
        self.assertEqual(dict(LoginRateLimit.objects.values_list("key", "expires_at")), expiry)

    def test_an_old_success_cannot_release_a_new_window(self):
        initial = timezone.now()
        with patch("apps.accounts.rate_limits.timezone.now", return_value=initial):
            old_reservations = reserve_login_attempt(self.request(), "famille")
        with patch("apps.accounts.rate_limits.timezone.now", return_value=initial + timedelta(seconds=901)):
            reserve_login_attempt(self.request(), "famille")
        release_login_attempt(old_reservations)
        self.assertEqual(set(LoginRateLimit.objects.values_list("attempts", flat=True)), {1})

    def test_limiter_failure_does_not_bypass_password_protection(self):
        with patch("apps.accounts.backends.reserve_login_attempt", side_effect=DatabaseError):
            self.assertIsNone(authenticate(self.request(), username="famille", password=PASSWORD))

    def test_admin_and_family_login_share_one_limit(self):
        client = Client()
        for _ in range(2):
            client.post(reverse("accounts:login"), {"username": "admin", "password": "wrong"})
        response = client.post(reverse("admin:login"), {"username": "admin", "password": PASSWORD, "next": reverse("admin:index")})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", client.session)

    def test_cleanup_removes_only_expired_counters(self):
        LoginRateLimit.objects.create(key="a" * 64, attempts=1, expires_at=timezone.now() - timedelta(seconds=1))
        active = LoginRateLimit.objects.create(key="b" * 64, attempts=1, expires_at=timezone.now() + timedelta(minutes=1))
        call_command("purge_login_attempts", stdout=StringIO())
        self.assertEqual(list(LoginRateLimit.objects.values_list("key", flat=True)), [active.key])
