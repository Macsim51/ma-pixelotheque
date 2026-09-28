from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.models import LoginRateLimit


class Command(BaseCommand):
    help = "Supprime les compteurs de connexion dont la fenêtre a expiré."

    def handle(self, *args, **options):
        deleted, _ = LoginRateLimit.objects.filter(expires_at__lte=timezone.now()).delete()
        self.stdout.write(f"{deleted} compteur(s) expiré(s) supprimé(s).")
