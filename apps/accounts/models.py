from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models


class User(AbstractUser):
    """Family roles never grant administration; only a superuser does."""

    class Role(models.TextChoices):
        FAMILY = "family", "Famille"
        GUEST = "guest", "Invité"

    class Theme(models.TextChoices):
        SYSTEM = "system", "Selon l’appareil"
        LIGHT = "light", "Clair"
        DARK = "dark", "Sombre"

    role = models.CharField("rôle", max_length=10, choices=Role.choices, default=Role.GUEST)
    theme = models.CharField("thème", max_length=10, choices=Theme.choices, default=Theme.SYSTEM)

    class Meta(AbstractUser.Meta):
        constraints = [
            models.CheckConstraint(condition=models.Q(role__in=["family", "guest"]), name="accounts_user_valid_role"),
            models.CheckConstraint(condition=models.Q(theme__in=["system", "light", "dark"]), name="accounts_user_valid_theme"),
            models.CheckConstraint(condition=models.Q(is_superuser=False) | models.Q(is_staff=True), name="accounts_superuser_is_staff"),
        ]

    def clean(self):
        super().clean()
        if self.is_superuser and not self.is_staff:
            raise ValidationError({
                "is_staff": "Un administrateur doit également avoir accès au site d’administration."
            })


class LoginRateLimit(models.Model):
    """A fixed-window counter containing failures and in-flight attempts.

    Keys are digests, never raw account names or addresses. Successful attempts
    release only their own reservation; they cannot erase somebody else's failures.
    """

    key = models.CharField(max_length=64, primary_key=True, editable=False)
    attempts = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        verbose_name = "limite de connexion"
        verbose_name_plural = "limites de connexion"
