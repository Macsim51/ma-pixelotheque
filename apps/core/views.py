"""Minimal operational and error endpoints, without leaking internal details."""

from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.db import DatabaseError, connection
from django.db.models import Count
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import get_template
from django.views.decorators.http import require_safe


@login_required
@require_safe
def home(request):
    if request.user.is_superuser or request.user.role == "family":
        return redirect("library:timeline")
    return redirect("library:album_list")


@require_safe
def healthz(request):
    """Probe the database without reporting its path, schema or server details."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


def _error(request, status, title, message):
    template = "sharing/error.html" if request.path_info.startswith("/s/") else "error.html"
    return render(request, template, {"error_code": status, "error_title": title, "error_message": message}, status=status)


def bad_request(request, exception):
    return _error(request, 400, "Cette demande ne peut pas être traitée", "Revenez à vos albums pour continuer.")


def permission_denied(request, exception):
    return _error(request, 403, "Cette action n’est pas disponible", "Votre compte ne permet pas d’effectuer cette action.")


def page_not_found(request, exception):
    return _error(request, 404, "Cette page n’est pas disponible", "Elle a peut-être été déplacée, ou vous n’y avez pas accès.")


def server_error(request):
    # Do not inspect request.user, sessions, messages or the static manifest:
    # the database or storage itself may have caused this server error.
    html = get_template("500.html").render({"home_url": f"{settings.APP_BASE_PATH}/"})
    return HttpResponse(html, status=500)


@login_required
@require_safe
def manage(request):
    """A small operational overview; sensitive settings stay in Django Admin."""
    if not request.user.is_superuser:
        raise PermissionDenied
    from apps.library.models import Album, MediaItem
    from apps.processing.models import ProcessingJob
    from .models import AppSetting

    counts = dict(ProcessingJob.objects.values_list("status").annotate(total=Count("pk")))
    return render(request, "manage.html", {
        "settings": AppSetting.load(),
        "album_count": Album.objects.count(),
        "media_count": MediaItem.objects.count(),
        "job_counts": counts,
        "errors": ProcessingJob.objects.filter(status="error").select_related("media").order_by("-created_at")[:10],
    })
