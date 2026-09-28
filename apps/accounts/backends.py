import logging

from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError

from .rate_limits import release_login_attempt, reserve_login_attempt


logger = logging.getLogger(__name__)


class RateLimitedModelBackend(ModelBackend):
    """One authentication path for Django Admin and the family interface.

    Denials use Django's generic invalid-login response and stop fallback
    backends. If the limiter cannot persist its counters, authentication closes
    rather than silently disabling the limit. Passwords and identifiers are not
    recorded in logs.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get("username")
        if username is None or password is None:
            return None
        try:
            reservations = reserve_login_attempt(request, username)
        except DatabaseError:
            logger.warning("Authentication rate limiter unavailable")
            raise PermissionDenied from None
        if reservations is None:
            raise PermissionDenied
        user = super().authenticate(request, username=username, password=password, **kwargs)
        if user is not None:
            try:
                release_login_attempt(reservations)
            except DatabaseError:
                logger.warning("Authentication rate limiter unavailable")
                raise PermissionDenied from None
        return user
