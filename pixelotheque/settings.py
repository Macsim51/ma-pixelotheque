"""Environment-driven settings shared by Docker and local development.

No production secret or host-specific storage path belongs in source control.
The deployment guide explains the reverse proxy's trust boundary and URL prefix.
"""

import os
import re
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    """Reject misspelled security flags instead of silently enabling a mode."""
    value = os.environ.get(name, str(default)).strip().lower()
    if value in {"true", "1", "yes", "on"}:
        return True
    if value in {"false", "0", "no", "off"}:
        return False
    raise ImproperlyConfigured(f"{name} doit valoir true ou false.")


def env_list(name):
    return [value.strip() for value in os.environ.get(name, "").split(",") if value.strip()]


DEBUG = env_bool("DJANGO_DEBUG")
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if len(SECRET_KEY) < 50 or SECRET_KEY.startswith("django-insecure-"):
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY doit contenir au moins 50 caractères aléatoires. "
        "Consultez le script de préparation décrit dans le README."
    )

ALLOWED_HOSTS = list(dict.fromkeys(env_list("DJANGO_ALLOWED_HOSTS") + ["localhost", "127.0.0.1", "[::1]"]))
if "*" in ALLOWED_HOSTS and not DEBUG:
    raise ImproperlyConfigured("Un hôte générique * n’est pas autorisé en production.")

CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
for origin in CSRF_TRUSTED_ORIGINS:
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path or parsed.query or parsed.fragment:
        raise ImproperlyConfigured("Les origines CSRF doivent être des origines http(s) sans chemin.")

APP_BASE_PATH = os.environ.get("APP_BASE_PATH", "").rstrip("/")
if APP_BASE_PATH and (
    not APP_BASE_PATH.startswith("/")
    or "//" in APP_BASE_PATH
    or any(character in APP_BASE_PATH for character in "?#%\\\r\n\t ")
    or any(segment in {".", ".."} for segment in APP_BASE_PATH.split("/"))
):
    raise ImproperlyConfigured("APP_BASE_PATH doit être vide ou un chemin tel que /ma-pixelotheque.")

# The proxy removes the prefix from PATH_INFO. Django still needs it to reverse
# links, including links generated outside a request by management commands.
FORCE_SCRIPT_NAME = APP_BASE_PATH or None
APP_HTTPS = env_bool("APP_HTTPS")
TRUST_PROXY_HTTPS = env_bool("TRUST_PROXY_HTTPS")

INSTALLED_APPS = [
    "apps.accounts.apps.PixelothequeAdminConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.accounts",
    "apps.library",
    "apps.core",
    "apps.processing",
    "apps.sharing",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.core.middleware.PrivateResponseMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "pixelotheque.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {
        "context_processors": [
            "django.template.context_processors.request",
            "django.contrib.auth.context_processors.auth",
            "django.contrib.messages.context_processors.messages",
        ],
    },
}]
WSGI_APPLICATION = "pixelotheque.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("DATABASE_PATH", str(BASE_DIR / "var" / "data" / "db.sqlite3")),
        "OPTIONS": {"timeout": 20, "transaction_mode": "IMMEDIATE"},
        "ATOMIC_REQUESTS": False,
    },
}
if os.environ.get("DATABASE_TEST_PATH"):
    DATABASES["default"]["TEST"] = {"NAME": os.environ["DATABASE_TEST_PATH"]}
# Keep SQLite's rollback journal until the actual container library has been
# checked for the WAL reset fix. A higher timeout is not a concurrency strategy.

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["apps.accounts.backends.RateLimitedModelBackend"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "library:album_list"
LOGOUT_REDIRECT_URL = "accounts:login"

LANGUAGE_CODE = "fr-fr"
TIME_ZONE = os.environ.get("TZ", "Europe/Paris")
try:
    ZoneInfo(TIME_ZONE)
except ZoneInfoNotFoundError as error:
    raise ImproperlyConfigured("TZ doit être un fuseau IANA valide, par exemple Europe/Paris.") from error
USE_I18N = True
USE_TZ = True

STATIC_URL = f"{APP_BASE_PATH}/static/"
STATIC_ROOT = Path(os.environ.get("STATIC_ROOT", str(BASE_DIR / "staticfiles")))
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", str(BASE_DIR / "var" / "media")))
MAP_TILE_URL = os.environ.get("MAP_TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
MAP_TILE_ATTRIBUTION = '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
# Deliberately no MEDIA_URL or media-serving URL pattern. User files will only
# be delivered by views after permission checks in the photo increment.

SESSION_COOKIE_NAME = "pixelotheque_sessionid"
CSRF_COOKIE_NAME = "pixelotheque_csrftoken"
SESSION_COOKIE_PATH = CSRF_COOKIE_PATH = f"{APP_BASE_PATH}/"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = APP_HTTPS
SECURE_SSL_REDIRECT = APP_HTTPS
SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
if APP_BASE_PATH:
    # WSGIRequest.path includes SCRIPT_NAME even when the proxy forwards the
    # stripped path. Docker's internal probe must work without TLS in both cases.
    SECURE_REDIRECT_EXEMPT.append(rf"^{re.escape(APP_BASE_PATH.lstrip('/'))}/healthz$")
if TRUST_PROXY_HTTPS:
    # Enable only when the controlled proxy overwrites this header and clients
    # cannot contact Gunicorn directly. Host is preserved separately by proxy.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 0  # Enable only after the final HTTPS domain is verified.
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
DATA_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 1000

# Do not add access logs containing URLs: future private share URLs contain
# bearer tokens. The deployment guide also documents proxy log filtering.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"private": {"()": "apps.core.logging.PrivateDataFormatter"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "private"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {"django": {"handlers": ["console"], "level": "WARNING", "propagate": False}},
}
