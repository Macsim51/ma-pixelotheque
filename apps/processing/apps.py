import warnings

from django.apps import AppConfig


class ProcessingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.processing"
    verbose_name = "Traitements des photos"

    def ready(self):
        from PIL import Image

        # Global hard ceiling, set once, never changed per threaded request.
        Image.MAX_IMAGE_PIXELS = 80_000_000
        warnings.filterwarnings("error", category=Image.DecompressionBombWarning)
