"""Public views never broaden a token's album scope with a logged-in session."""

from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import F, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_cache_control
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from apps.core.models import AppSetting
from apps.library.models import Album, MediaItem
from apps.library.permissions import can_manage_album
from apps.processing.storage import media_response

from .forms import SharedLinkForm
from .models import SharedLink, digest_token


def _unavailable(request):
    return render(request, "sharing/unavailable.html", status=404)


def private_share_response(view):
    """Even missing shares and media return the isolated, non-cacheable shell."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            response = view(request, *args, **kwargs)
        except (Http404, PermissionDenied):
            response = _unavailable(request)
        patch_cache_control(response, private=True, no_store=True)
        response["Referrer-Policy"] = "no-referrer"
        response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self'; style-src 'self'; script-src 'none'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        )
        return response
    return wrapped


def _link(token):
    digest = digest_token(token)
    if digest is None:
        raise Http404
    return get_object_or_404(SharedLink.objects.available().select_related("album"), token_digest=digest)


def _photos(link):
    # Deliberately do not use visible_to(request.user): the link is the only
    # authority on public routes, even for the instance's administrator.
    return MediaItem.objects.filter(albums=link.album, status=MediaItem.Status.READY).order_by("-sort_date", "-pk")


def _managed_album(request, album_id):
    album = get_object_or_404(Album.objects.visible_to(request.user), pk=album_id)
    if not can_manage_album(request.user, album):
        raise PermissionDenied
    if not request.user.is_superuser and not AppSetting.load().allow_family_sharing:
        raise PermissionDenied
    return album


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def manage(request, album_id):
    album = _managed_album(request, album_id)
    form = SharedLinkForm(request.POST if request.method == "POST" else None)
    created_url = None
    if request.method == "POST" and form.is_valid():
        # Return the token only in this response. Never persist it in a session,
        # message, model, redirect query string or logging call.
        _, token = SharedLink.issue(album=album, creator=request.user, **form.cleaned_data)
        created_url = request.build_absolute_uri(reverse("sharing:album", kwargs={"token": token}))
        form = SharedLinkForm()
    page = Paginator(album.shared_links.all(), 60).get_page(request.GET.get("page"))
    response = render(request, "sharing/manage.html", {
        "album": album, "form": form, "page_obj": page, "created_url": created_url,
    })
    # The one-time URL also must not be sent as a referrer from this response.
    response["Referrer-Policy"] = "no-referrer"
    return response


@never_cache
@login_required
@require_POST
def change_link(request, album_id, link_id, action):
    album = _managed_album(request, album_id)
    link = get_object_or_404(SharedLink, pk=link_id, album=album)
    if action == "revoke":
        SharedLink.objects.filter(pk=link.pk, revoked_at__isnull=True).update(active=False, revoked_at=timezone.now())
        messages.success(request, "Le lien a été révoqué définitivement.")
    elif action == "disable":
        SharedLink.objects.filter(pk=link.pk).update(active=False)
        messages.success(request, "Le lien a été désactivé.")
    elif action == "enable":
        # A revoked token is never reusable; an expired one is never re-enabled.
        changed = SharedLink.objects.filter(pk=link.pk, revoked_at__isnull=True).filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
        ).update(active=True)
        if changed:
            messages.success(request, "Le lien a été réactivé.")
        else:
            messages.error(request, "Ce lien a expiré ou a été révoqué. Créez un nouveau lien.")
    else:
        raise Http404
    return redirect("sharing:manage", album_id=album.pk)


@private_share_response
@require_safe
def shared_album(request, token):
    link = _link(token)
    page = Paginator(_photos(link), 60).get_page(request.GET.get("page"))
    if request.method == "GET":
        SharedLink.objects.available().filter(pk=link.pk).update(
            last_accessed_at=timezone.now(), access_count=F("access_count") + 1,
        )
    return render(request, "sharing/album.html", {"share": link, "token": token, "page_obj": page})


@private_share_response
@require_safe
def shared_photo(request, token, media_id):
    link = _link(token)
    photos = _photos(link)
    photo = get_object_or_404(photos.prefetch_related("tags"), pk=media_id)
    previous = photos.filter(
        Q(sort_date__gt=photo.sort_date) | Q(sort_date=photo.sort_date, pk__gt=photo.pk)
    ).order_by("sort_date", "pk").first()
    following = photos.filter(
        Q(sort_date__lt=photo.sort_date) | Q(sort_date=photo.sort_date, pk__lt=photo.pk)
    ).first()
    return render(request, "sharing/photo.html", {
        "share": link, "token": token, "photo": photo,
        "previous_photo": previous, "next_photo": following,
    })


@private_share_response
@require_safe
def shared_file(request, token, media_id, variant):
    link = _link(token)
    if variant not in {"thumbnail", "preview", "original"}:
        raise Http404
    if variant == "original" and not link.allow_download:
        raise Http404
    photo = get_object_or_404(_photos(link), pk=media_id)
    return media_response(request, photo, variant, allow_original=link.allow_download)


@private_share_response
@require_safe
def unavailable(request, **kwargs):
    return _unavailable(request)
