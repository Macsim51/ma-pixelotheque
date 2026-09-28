"""Private file keys are never public URLs. Callers must enforce their own ACL."""

import os
import stat
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.http import FileResponse, Http404


CONTENT_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def media_path(key, *, category=None):
    """Confine storage operations even if a stored key is malformed."""
    if not key or "\\" in key or "\x00" in key:
        raise ValueError("Clé de stockage invalide.")
    relative = PurePosixPath(key)
    if not relative.parts or relative.is_absolute() or ".." in relative.parts or (category and relative.parts[0] != category):
        raise ValueError("Clé de stockage invalide.")
    root = Path(settings.MEDIA_ROOT).resolve()
    path = root.joinpath(*relative.parts)
    if any(part.is_symlink() for part in [path, *path.parents] if part != root and root in part.parents):
        raise ValueError("Les liens symboliques ne sont pas des médias.")
    if not path.resolve().is_relative_to(root):
        raise ValueError("Clé de stockage invalide.")
    return path


def media_response(request, media, variant, allow_original=True):
    if media.status != "ready":
        raise Http404
    fields = {"original": ("original_key", "originals"), "thumbnail": ("thumbnail_key", "thumbnails"), "preview": ("preview_key", "previews")}
    if variant not in fields or (variant == "original" and not allow_original):
        raise Http404
    field, category = fields[variant]
    try:
        path = media_path(getattr(media, field), category=category)
        content_type = CONTENT_TYPES[path.suffix.lower()]
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("Le média doit être un fichier régulier.")
            stream = os.fdopen(descriptor, "rb")
        except Exception:
            os.close(descriptor)
            raise
    except (ValueError, KeyError, OSError):
        raise Http404 from None
    response = FileResponse(
        stream, content_type=content_type, as_attachment=variant == "original",
        filename=media.original_name if variant == "original" else f"{media.pk}-{variant}.webp",
    )
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Referrer-Policy"] = "no-referrer"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
