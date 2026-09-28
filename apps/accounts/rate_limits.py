"""Database-backed login limits shared by normal and administrator logins.

Only REMOTE_ADDR is read: forwarded headers are not trusted here. Every password
check reserves capacity atomically first, so concurrent requests cannot bypass a
limit by starting before an earlier password check has completed. A fixed window
does not extend indefinitely when somebody keeps retrying a blocked account.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import ipaddress
import unicodedata

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import LoginRateLimit


@dataclass(frozen=True)
class AttemptReservation:
    key: str
    expires_at: datetime


def _key(scope, *values):
    return hashlib.sha256("\0".join((scope, *values)).encode("utf-8")).hexdigest()


def _address(request):
    raw = request.META.get("REMOTE_ADDR", "") if request is not None else ""
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return "unknown"


def reserve_login_attempt(request, username):
    """Return reservations, or None if any budget is exhausted.

    Limits apply separately to IP, account and account/IP pair. The larger IP
    budget allows a household to share one address. Expired rows are reset with
    an UPDATE before a read, which also avoids SQLite's read-to-write upgrade
    pattern. No transaction remains open during password hashing.
    """

    normalized = unicodedata.normalize("NFKC", str(username or "")[:512]).strip().casefold()
    address = _address(request)
    window = max(1, int(getattr(settings, "LOGIN_RATE_LIMIT_WINDOW_SECONDS", 900)))
    ip_limit = max(1, int(getattr(settings, "LOGIN_RATE_LIMIT_IP_ATTEMPTS", 60)))
    username_limit = max(1, int(getattr(settings, "LOGIN_RATE_LIMIT_USERNAME_ATTEMPTS", 20)))
    pair_limit = max(1, int(getattr(settings, "LOGIN_RATE_LIMIT_ATTEMPTS", 5)))
    budgets = (
        (_key("ip", address), ip_limit),
        (_key("username", normalized), username_limit),
        (_key("pair", normalized, address), pair_limit),
    )
    now = timezone.now()
    deadline = now + timedelta(seconds=window)
    reservations = []
    with transaction.atomic():
        for key, limit in budgets:
            LoginRateLimit.objects.filter(key=key, expires_at__lte=now).update(attempts=0, expires_at=deadline)
            bucket, _ = LoginRateLimit.objects.get_or_create(key=key, defaults={"expires_at": deadline})
            changed = LoginRateLimit.objects.filter(key=key, attempts__lt=limit).update(attempts=F("attempts") + 1)
            if not changed:
                # Earlier scopes count this rejected request too. In particular,
                # exhausting a pair does not let that client bypass the IP cap.
                return None
            reservations.append(AttemptReservation(key, bucket.expires_at))
    return reservations


def release_login_attempt(reservations):
    """Release only this successful request and never a renewed window."""

    with transaction.atomic():
        for reservation in reservations:
            LoginRateLimit.objects.filter(
                key=reservation.key,
                expires_at=reservation.expires_at,
                attempts__gt=0,
            ).update(attempts=F("attempts") - 1)
