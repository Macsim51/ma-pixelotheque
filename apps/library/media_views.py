from datetime import date, timedelta
import uuid
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Avg, Case, Count, F, FloatField, Q, Value, When
from django.db.models.functions import Floor
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST, require_safe

from apps.core.models import AppSetting
from .forms import MediaItemForm, SearchForm
from .models import Album, Favorite, MediaItem
from .permissions import can_edit_media
from .selectors import apply_search, favorite_media, in_bbox, local_midnight, parse_bbox, ready_media


SEARCH_KEYS = ("q", "date_from", "date_to", "uploader", "camera", "gps", "favorites")


def require_global_access(user):
    if not user.is_active or not (user.is_superuser or user.role == "family"):
        raise PermissionDenied


def query_string(request, **changes):
    params = {key: request.GET[key] for key in SEARCH_KEYS if request.GET.get(key)}
    params.update(changes)
    return urlencode(params)


def grid_context(request, queryset, *, context="timeline", album=None):
    page = Paginator(queryset, 60).get_page(request.GET.get("page"))
    navigation = {"context": context}
    if album is not None:
        navigation["album"] = str(album.pk)
    return {
        "page_obj": page,
        "media_items": page,
        "navigation_query": query_string(request, **navigation),
        "pagination_query": query_string(request),
    }


def period_bounds(year, month=None, day=None):
    try:
        if not 2 <= year <= 9998:
            raise ValueError
        start = date(year, month if month is not None else 1, day if day is not None else 1)
        if day is not None:
            end = start + timedelta(days=1)
        elif month is not None:
            end = date(year + (month == 12), month % 12 + 1, 1)
        else:
            end = date(year + 1, 1, 1)
    except (ValueError, OverflowError):
        raise Http404 from None
    return start, end


def representative_period(queryset, start, end, *, level):
    """A fixed number of indexed range queries; no random sort or global load."""
    if level == "year":
        edges = [local_midnight(date(start.year, month, 1)) for month in range(1, 13)] + [local_midnight(end)]
    elif level == "month":
        edges = [local_midnight(start + timedelta(days=offset)) for offset in range((end - start).days + 1)]
    else:
        midnight = local_midnight(start)
        edges = [midnight + timedelta(hours=hour) for hour in range(25)]
    selected = []
    for lower, upper in reversed(list(zip(edges, edges[1:]))):
        photo = queryset.filter(sort_date__gte=lower, sort_date__lt=upper).order_by("sort_date", "id").first()
        if photo is not None:
            selected.append(photo)
    return selected


@login_required
@require_safe
def timeline(request, year=None, month=None, day=None):
    require_global_access(request.user)
    queryset = ready_media(request.user)
    context = {"page_title": "Photos", "is_timeline": True}
    if year is not None:
        start, end = period_bounds(year, month, day)
        queryset = queryset.filter(sort_date__gte=local_midnight(start), sort_date__lt=local_midnight(end))
        level = "day" if day is not None else "month" if month is not None else "year"
        context.update({"period_start": start, "period_level": level, "period_year": year, "period_month": month})
        context["page_title"] = str(year) if level == "year" else start.strftime("%d/%m/%Y") if level == "day" else start.strftime("%m/%Y")
        if request.GET.get("all") != "1":
            context.update({
                "media_items": representative_period(queryset, start, end, level=level),
                "representative": True,
                "navigation_query": urlencode({"context": "timeline", "date_from": start.isoformat(), "date_to": (end - timedelta(days=1)).isoformat()}),
            })
        else:
            context.update(grid_context(request, queryset))
            context["pagination_query"] = "all=1"
            context["navigation_query"] = urlencode({"context": "timeline", "date_from": start.isoformat(), "date_to": (end - timedelta(days=1)).isoformat()})
    else:
        context.update(grid_context(request, queryset))
    return render(request, "library/timeline.html", context)


@login_required
@require_safe
def search(request):
    require_global_access(request.user)
    form = SearchForm(request.GET, user=request.user)
    queryset = ready_media(request.user)
    queryset = apply_search(queryset, request.user, form.cleaned_data) if form.is_valid() else queryset.none()
    context = grid_context(request, queryset, context="search")
    context.update({"page_title": "Recherche", "search_form": form})
    return render(request, "library/search.html", context)


@login_required
@require_safe
def favorites(request):
    queryset = favorite_media(ready_media(request.user), request.user)
    context = grid_context(request, queryset, context="favorites")
    context["page_title"] = "Mes favoris"
    return render(request, "library/media_list.html", context)


def photo_navigation(request, media):
    queryset = MediaItem.objects.visible_to(request.user)
    album_id = request.GET.get("album")
    context_name = request.GET.get("context", "timeline")
    if album_id:
        try:
            album_id = uuid.UUID(album_id)
        except (ValueError, AttributeError):
            raise Http404 from None
        album = get_object_or_404(Album.objects.visible_to(request.user), pk=album_id)
        queryset = queryset.filter(albums=album)
        backlink = reverse("library:album_detail", args=[album.pk])
        params = urlencode({"context": "album", "album": str(album.pk)})
    elif context_name == "favorites":
        queryset = favorite_media(queryset.filter(status=MediaItem.Status.READY), request.user)
        backlink = reverse("library:favorites")
        params = "context=favorites"
    elif context_name == "map":
        require_global_access(request.user)
        try:
            bounds = parse_bbox(request.GET.get("bbox", ""))
        except ValidationError:
            raise Http404 from None
        queryset = in_bbox(queryset.filter(status=MediaItem.Status.READY), bounds)
        params = urlencode({"context": "map", "bbox": request.GET["bbox"]})
        backlink = reverse("library:map_photos") + "?" + urlencode({"bbox": request.GET["bbox"]})
    elif request.user.role == "guest" and not request.user.is_superuser:
        album = Album.objects.visible_to(request.user).filter(media_items=media).first()
        if album is None:
            raise Http404
        queryset = queryset.filter(albums=album)
        backlink = reverse("library:album_detail", args=[album.pk])
        params = urlencode({"context": "album", "album": str(album.pk)})
    else:
        require_global_access(request.user)
        if context_name not in ("timeline", "search"):
            raise Http404
        form = SearchForm(request.GET, user=request.user)
        if not form.is_valid():
            raise Http404
        queryset = apply_search(queryset, request.user, form.cleaned_data)
        # Direct links to a processing photo remain useful to its uploader.
        if media.status == MediaItem.Status.READY:
            queryset = queryset.filter(status=MediaItem.Status.READY)
        backlink = reverse("library:search" if context_name == "search" else "library:timeline")
        params = query_string(request, context=context_name)
        if context_name == "search" and query_string(request):
            backlink += "?" + query_string(request)
    if not queryset.filter(pk=media.pk).exists():
        raise Http404
    previous = queryset.filter(Q(sort_date__gt=media.sort_date) | Q(sort_date=media.sort_date, pk__gt=media.pk)).order_by("sort_date", "id").first()
    following = queryset.filter(Q(sort_date__lt=media.sort_date) | Q(sort_date=media.sort_date, pk__lt=media.pk)).first()
    return {"previous_media": previous, "next_media": following, "navigation_query": params, "back_url": backlink}


@login_required
@require_safe
def media_detail(request, pk):
    media = get_object_or_404(MediaItem.objects.visible_to(request.user).with_favorite(request.user).select_related("uploader").prefetch_related("tags"), pk=pk)
    context = photo_navigation(request, media)
    context.update({
        "media": media,
        "visible_albums": Album.objects.visible_to(request.user).filter(media_items=media),
        "can_edit_media": can_edit_media(request.user, media),
        "can_download": media.status == MediaItem.Status.READY and (request.user.is_superuser or request.user.role == "family"),
        "favorite_next": request.get_full_path(),
    })
    return render(request, "library/media_detail.html", context)


@login_required
def media_edit(request, pk):
    if request.method not in ("GET", "POST"):
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(["GET", "POST"])
    media = get_object_or_404(MediaItem.objects.visible_to(request.user), pk=pk)
    if not can_edit_media(request.user, media):
        raise PermissionDenied
    form = MediaItemForm(request.POST if request.method == "POST" else None, user=request.user, instance=media)
    if request.method == "POST" and form.is_valid():
        try:
            form.save()
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, "La photo a été mise à jour.")
            if MediaItem.objects.visible_to(request.user).filter(pk=media.pk).exists():
                return redirect("library:media_detail", pk=media.pk)
            return redirect("library:album_list")
    return render(request, "library/media_form.html", {"form": form, "media": media})


@login_required
@require_safe
def media_file(request, pk, variant):
    from apps.processing.storage import media_response

    media = get_object_or_404(ready_media(request.user), pk=pk)
    if variant not in ("thumbnail", "preview", "original"):
        raise Http404
    allow_original = request.user.is_superuser or request.user.role == "family"
    if variant == "original" and not allow_original:
        raise PermissionDenied
    return media_response(request, media, variant, allow_original=allow_original)


@login_required
@require_POST
@transaction.atomic
def favorite_toggle(request, pk):
    media = get_object_or_404(ready_media(request.user), pk=pk)
    favorite, created = Favorite.objects.get_or_create(user=request.user, media=media)
    if not created:
        favorite.delete()
    media.is_favorite = created
    target = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        target = reverse("library:media_detail", args=[media.pk])
    if request.headers.get("HX-Request") == "true":
        response = render(request, "library/_favorite_button.html", {"media": media, "favorite_next": target})
        response["Cache-Control"] = "private, no-store"
        response["Vary"] = "HX-Request, Cookie"
        return response
    return redirect(target)


@login_required
@require_safe
def map_view(request):
    require_global_access(request.user)
    response = render(request, "library/map.html", {"map_config": {
        "tile_url": getattr(settings, "MAP_TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png"),
        "tile_attribution": getattr(settings, "MAP_TILE_ATTRIBUTION", "© OpenStreetMap contributors"),
        "max_zoom": 19,
    }})
    response["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


@login_required
@require_safe
def map_data(request):
    require_global_access(request.user)
    try:
        bounds = parse_bbox(request.GET.get("bbox", "-180,-85,180,85"))
        zoom = int(request.GET.get("zoom", "2"))
        if not 0 <= zoom <= 20:
            raise ValidationError("Niveau de zoom invalide.")
    except (ValidationError, ValueError):
        return JsonResponse({"error": "Zone ou niveau de zoom invalide."}, status=400)
    west, south, east, north = bounds
    span = east - west if east > west else east + 360 - west
    if span <= 0:
        return JsonResponse({"error": "Zone invalide."}, status=400)
    longitude = F("longitude") if west < east else Case(When(longitude__lt=west, then=F("longitude") + 360.0), default=F("longitude"), output_field=FloatField())
    x_step, y_step = span / 15, (north - south) / 15
    groups = in_bbox(ready_media(request.user), bounds).annotate(
        cell_x=Floor((longitude - Value(west)) / Value(x_step)),
        cell_y=Floor((F("latitude") - Value(south)) / Value(y_step)),
    ).values("cell_x", "cell_y").annotate(count=Count("id"), lat=Avg("latitude"), lng=Avg(longitude)).order_by()
    cells = []
    for group in groups[:300]:
        # At most 16*16 cells, including values on the viewport's upper edges.
        x, y = int(group["cell_x"]), int(group["cell_y"])
        left, right = west + x * x_step, min(west + (x + 1) * x_step, west + span)
        bottom, top = south + y * y_step, min(south + (y + 1) * y_step, north)
        if right == left:
            left = max(west, left - x_step)
        if top == bottom:
            bottom = max(south, bottom - y_step)
        wrap = lambda value: value - 360 if value > 180 else value
        cells.append({"lat": group["lat"], "lng": wrap(group["lng"]), "count": group["count"], "bbox": [wrap(left), bottom, wrap(right), top]})
    response = JsonResponse({"cells": cells, "total": sum(cell["count"] for cell in cells)})
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_safe
def map_photos(request):
    require_global_access(request.user)
    try:
        bounds = parse_bbox(request.GET.get("bbox", ""))
    except ValidationError:
        raise Http404 from None
    context = grid_context(request, in_bbox(ready_media(request.user), bounds))
    context.update({"page_title": "Photos de ce lieu", "pagination_query": urlencode({"bbox": request.GET["bbox"]}), "navigation_query": urlencode({"context": "map", "bbox": request.GET["bbox"]}), "back_url": reverse("library:map")})
    return render(request, "library/media_list.html", context)
