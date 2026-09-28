from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.core.files.uploadhandler import MemoryFileUploadHandler
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.views.decorators.http import require_http_methods

from apps.library.models import Album
from apps.library.permissions import can_create_album

from .forms import UploadForm
from .services import processing_options, upload_to_album
from .upload_handlers import BoundedUploadHandler, PrivateTemporaryUploadHandler


@login_required
@require_http_methods(["GET", "POST"])
@csrf_exempt
def upload(request):
    if not can_create_album(request.user):
        raise PermissionDenied
    options = processing_options()
    if request.method == "POST":
        # CSRF's multipart read must happen AFTER installing the byte limiter.
        # The inner view is explicitly CSRF-protected; no mutation bypasses it.
        request.upload_handlers = [
            BoundedUploadHandler(request, options["max_bytes"]),
            MemoryFileUploadHandler(request), PrivateTemporaryUploadHandler(request),
        ]
    return protected_upload(request, options)


@csrf_protect
def protected_upload(request, options):
    wants_json = "application/json" in request.headers.get("Accept", "")
    form = UploadForm(
        request.POST if request.method == "POST" else None,
        request.FILES if request.method == "POST" else None,
        user=request.user, initial={"album": request.GET.get("album")},
    )
    errors, uploaded = [], []
    album = None
    if request.method == "POST" and getattr(request, "upload_rejection", ""):
        form.is_valid()
        form.add_error(None, request.upload_rejection)
    elif request.method == "POST" and form.is_valid():
        album = form.cleaned_data["album"]
        if album is None:
            album = Album.objects.create(owner=request.user, title=form.cleaned_data["new_album"])
        successes = 0
        for photo in form.cleaned_data["files"]:
            try:
                media = upload_to_album(photo, request.user, album)
                uploaded.append({"id": str(media.pk), "name": media.original_name})
                successes += 1
            except ValidationError as error:
                errors.append((photo.name, " ".join(error.messages)))
            except OSError:
                errors.append((photo.name, "Le fichier n’a pas pu être enregistré. Réessayez plus tard."))
        # The enhanced uploader renders its own summary. Queueing a Django
        # message per file would flood the next page after a large batch.
        if successes and not wants_json:
            messages.success(request, f"{successes} photo(s) ajoutée(s). Leur vérification est en attente.")
        if wants_json:
            return JsonResponse({"album_id": str(album.pk), "uploaded": uploaded, "errors": [{"name": name, "message": message} for name, message in errors]}, status=200 if successes else 400)
        if not errors:
            return redirect("library:album_detail", pk=album.pk)
        form = UploadForm(user=request.user, initial={"album": album})
    if request.method == "POST" and wants_json:
        return JsonResponse({"album_id": None, "uploaded": [], "errors": [{"name": field, "message": " ".join(field_errors)} for field, field_errors in form.errors.items()]}, status=400)
    return render(request, "processing/upload.html", {"form": form, "upload_errors": errors, "max_upload_mb": options["max_bytes"] // (1024 * 1024)})
