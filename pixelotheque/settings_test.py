"""Test-only configuration. Never select this module for a deployed instance."""

import os
import tempfile
from pathlib import Path

# These public constants are deliberately scoped to a test settings module.
os.environ.setdefault("DJANGO_SECRET_KEY", "pixelotheque-test-only-key-not-for-deployment-000000000000000000")
os.environ.setdefault("DATABASE_TEST_PATH", str(Path(tempfile.gettempdir()) / f"pixelotheque-tests-{os.getpid()}.sqlite3"))

from .settings import *  # noqa: F403,E402

ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1", "[::1]"]
# A Compose test run inherits the installed site's environment. The test client
# has no reverse proxy, so isolate it from that site's URL/HTTPS configuration.
# Dedicated integration tests override these settings explicitly.
APP_BASE_PATH = ""
FORCE_SCRIPT_NAME = None
APP_HTTPS = False
TRUST_PROXY_HTTPS = False
SECURE_SSL_REDIRECT = False
SECURE_PROXY_SSL_HEADER = None
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = False
SESSION_COOKIE_PATH = CSRF_COOKIE_PATH = "/"
STATIC_URL = "/static/"
CSRF_TRUSTED_ORIGINS = []
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
