"""Check that deployment plumbing does not disclose data or break subpaths."""

from unittest.mock import patch
import logging

from django.db import DatabaseError
from django.test import RequestFactory, TestCase, override_settings
from django.urls import clear_url_caches, reverse, set_script_prefix

from apps.accounts.models import User
from apps.core.views import server_error
from apps.core.logging import PrivateDataFormatter


class HealthTests(TestCase):
    def test_server_error_can_render_without_a_working_database(self):
        request = RequestFactory().get("/")
        with patch("django.db.backends.utils.CursorWrapper.execute", side_effect=DatabaseError("private/path.sqlite3")):
            response = server_error(request)
        self.assertEqual(response.status_code, 500)
        self.assertIn("Un problème est survenu".encode(), response.content)
        self.assertNotIn(b"private/path", response.content)

    def test_probe_is_public_but_does_not_report_environment(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertIn("no-store", response["Cache-Control"])

    def test_probe_reports_unavailable_without_exception_details(self):
        with patch("apps.core.views.connection.cursor", side_effect=DatabaseError("private/path.sqlite3")):
            response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})
        self.assertNotContains(response, "private/path", status_code=503)

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_internal_probe_does_not_require_https(self):
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        self.assertEqual(self.client.get("/accounts/login/").status_code, 301)


class RoutingTests(TestCase):
    def test_anonymous_home_requires_authentication(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse("accounts:login")))

    def test_private_storage_is_never_served_by_django(self):
        for path in ("/media/originals/example.jpg", "/data/db.sqlite3", "/.env", "/.git/config"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_error_page_has_no_internal_path(self):
        response = self.client.get("/missing-album/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Cette page n’est pas disponible", status_code=404)
        self.assertIn("no-store", response["Cache-Control"])

    @override_settings(FORCE_SCRIPT_NAME="/ma-pixelotheque")
    def test_urls_work_after_proxy_strips_prefix(self):
        # The external path is /ma-pixelotheque/albums/. Proxy forwards /albums/
        # and Django adds SCRIPT_NAME back to all generated links and redirects.
        try:
            # ClientHandler bypasses WSGIHandler.__call__, which normally sets
            # this thread-local resolver prefix before it dispatches a request.
            set_script_prefix("/ma-pixelotheque/")
            response = self.client.get("/albums/", SCRIPT_NAME="/ma-pixelotheque")
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.url.startswith("/ma-pixelotheque/accounts/login/"))
            user = User.objects.create_user(username="prefix-family", password="a-long-test-password", role="family")
            self.client.force_login(user)
            response = self.client.get("/albums/", SCRIPT_NAME="/ma-pixelotheque")
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, '/ma-pixelotheque/accounts/')
        finally:
            set_script_prefix("/")
            clear_url_caches()

    def test_staff_without_superuser_cannot_enter_advanced_admin(self):
        user = User.objects.create_user(username="ordinary-staff", password="a-long-test-password", role="family", is_staff=True)
        self.client.force_login(user)
        response = self.client.get("/admin/")
        self.assertNotEqual(response.status_code, 200)


class PrivateLogTests(TestCase):
    def test_share_bearer_token_is_removed_after_message_interpolation(self):
        token = "confidential-token-not-for-logs-123456789"
        record = logging.LogRecord("django.request", logging.WARNING, "", 0, "Not found: %s", (f"/ma-pixelotheque/s/{token}/files/image/preview/",), None)
        rendered = PrivateDataFormatter().format(record)
        self.assertNotIn(token, rendered)
        self.assertIn("/s/[partage]/files/", rendered)

    def test_management_route_is_not_a_bearer_token(self):
        record = logging.LogRecord("django.request", logging.INFO, "", 0, "/s/manage/album/", (), None)
        self.assertEqual(PrivateDataFormatter().format(record), "/s/manage/album/")
