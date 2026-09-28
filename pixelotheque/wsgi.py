"""WSGI entry point used by Gunicorn."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pixelotheque.settings")
application = get_wsgi_application()
