"""An album-scoped bearer capability, independent of account permissions."""

import hashlib
import re
import secrets

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")


def digest_token(token):
    """Return no lookup key for malformed tokens, never store their plaintext."""
    if not isinstance(token, str) or TOKEN_PATTERN.fullmatch(token) is None:
        return None
    return hashlib.sha256(token.encode("ascii")).hexdigest()


class SharedLinkQuerySet(models.QuerySet):
    def available(self, at=None):
        at = at or timezone.now()
        return self.filter(active=True, revoked_at__isnull=True).filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=at)
        )


class SharedLink(models.Model):
    album = models.ForeignKey("library.Album", on_delete=models.CASCADE, related_name="shared_links")
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_shared_links")
    token_digest = models.CharField(max_length=64, unique=True, editable=False)
    active = models.BooleanField("actif", default=True)
    expires_at = models.DateTimeField("expiration", null=True, blank=True)
    allow_download = models.BooleanField("téléchargement des originaux", default=False)
    created_at = models.DateTimeField("création", auto_now_add=True)
    last_accessed_at = models.DateTimeField("dernière ouverture", null=True, blank=True)
    access_count = models.PositiveBigIntegerField("ouvertures", default=0)
    revoked_at = models.DateTimeField("révocation définitive", null=True, blank=True, editable=False)

    objects = SharedLinkQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at", "-pk")
        verbose_name = "lien privé"
        verbose_name_plural = "liens privés"
        constraints = [
            models.CheckConstraint(
                condition=Q(revoked_at__isnull=True) | Q(active=False),
                name="sharing_revoked_link_inactive",
            ),
        ]

    def __str__(self):
        return f"Lien privé {self.pk}"

    @classmethod
    def issue(cls, *, album, creator, expires_at=None, allow_download=False):
        """Return the new row and its only plaintext token; callers must authorize."""
        token = secrets.token_urlsafe(32)
        link = cls.objects.create(
            album=album, creator=creator, token_digest=digest_token(token),
            expires_at=expires_at, allow_download=allow_download,
        )
        return link, token

    @property
    def is_expired(self):
        return self.expires_at is not None and self.expires_at <= timezone.now()
