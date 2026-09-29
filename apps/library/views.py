import uuid
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_safe

from .covers import AlbumCovers
from .forms import AlbumCoverForm, AlbumForm, AlbumGroupingForm
from .models import Album, MediaItem
from .permissions import can_create_album, can_manage_album


def album_cards(user):
    media = MediaItem.objects.visible_to(user).filter(albums=OuterRef("pk"))
    counts = media.order_by().values("albums").annotate(total=Count("pk")).values("total")
    children = Album.objects.visible_to(user).filter(parent_id=OuterRef("pk"))
    child_counts = children.order_by().values("parent_id").annotate(total=Count("pk")).values("total")
    return Album.objects.visible_to(user).select_related("owner").annotate(
        media_count=Coalesce(Subquery(counts[:1]), Value(0)),
        child_count=Coalesce(Subquery(child_counts[:1]), Value(0)),
    )


def album_ancestors(album, user):
    ancestors = []
    seen = {album.pk}
    parent_id = album.parent_id
    while parent_id is not None and parent_id not in seen:
        parent = Album.objects.visible_to(user).filter(pk=parent_id).first()
        if parent is None:
            break
        ancestors.append(parent)
        seen.add(parent_id)
        parent_id = parent.parent_id
    return list(reversed(ancestors))


@login_required
@require_safe
def album_list(request):
    # An authorized child remains reachable if its parent is not visible.
    albums = album_cards(request.user).exclude(
        parent_id__in=Album.objects.visible_to(request.user).values("pk")
    )
    page = Paginator(albums, 24).get_page(request.GET.get("page"))
    AlbumCovers(request.user).apply(page)
    return render(
        request,
        "library/album_list.html",
        {"page_obj": page, "can_create_album": can_create_album(request.user)},
    )


@login_required
@require_safe
def album_detail(request, pk):
    from apps.core.models import AppSetting
    from .media_views import grid_context

    album = get_object_or_404(
        Album.objects.visible_to(request.user).select_related("owner"), pk=pk
    )
    context = grid_context(request, MediaItem.objects.visible_to(request.user).filter(albums=album), context="album", album=album)
    can_manage = can_manage_album(request.user, album)
    subalbums = Paginator(album_cards(request.user).filter(parent=album), 24).get_page(request.GET.get("albums_page"))
    AlbumCovers(request.user).apply(subalbums)
    context.update({
        "album": album,
        "ancestors": album_ancestors(album, request.user),
        "subalbums_page": subalbums,
        "album_pagination_query": f"page={context['page_obj'].number}&",
        "pagination_query": f"albums_page={subalbums.number}",
        "can_manage_album": can_manage,
        "can_upload_album": album.can_upload(request.user),
        "can_share_album": can_manage and (request.user.is_superuser or AppSetting.load().allow_family_sharing),
    })
    return render(
        request,
        "library/album_detail.html",
        context,
    )


@login_required
@require_http_methods(["GET", "POST"])
def album_create(request):
    if not can_create_album(request.user):
        raise PermissionDenied
    parent = None
    if request.GET.get("parent"):
        try:
            parent_id = uuid.UUID(request.GET["parent"])
        except ValueError:
            raise Http404 from None
        parent = get_object_or_404(Album.objects.visible_to(request.user), pk=parent_id)
        if not can_manage_album(request.user, parent):
            raise PermissionDenied
    form = AlbumForm(
        request.POST if request.method == "POST" else None,
        user=request.user,
        initial={"parent": parent},
    )
    if request.method == "POST" and form.is_valid():
        try:
            album = form.save()
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, "L’album a été créé.")
            return redirect("library:album_detail", pk=album.pk)
    return render(request, "library/album_form.html", {"form": form, "is_creation": True, "creation_parent": parent})


@login_required
@require_http_methods(["GET", "POST"])
def album_edit(request, pk):
    album = get_object_or_404(Album.objects.visible_to(request.user), pk=pk)
    if not can_manage_album(request.user, album):
        raise PermissionDenied
    form = AlbumForm(
        request.POST if request.method == "POST" else None,
        user=request.user,
        instance=album,
    )
    if request.method == "POST" and form.is_valid():
        try:
            album = form.save()
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, "Les modifications ont été enregistrées.")
            return redirect("library:album_detail", pk=album.pk)
    return render(
        request,
        "library/album_form.html",
        {"form": form, "album": album, "is_creation": False},
    )


@login_required
@require_http_methods(["GET", "POST"])
def album_group(request, pk):
    album = get_object_or_404(Album.objects.visible_to(request.user), pk=pk)
    form = AlbumGroupingForm(
        request.POST if request.method == "POST" else None,
        user=request.user,
        album=album,
    )
    if request.method == "POST" and form.is_valid():
        try:
            form.save()
        except ValidationError:
            form.add_error("albums", "L’organisation des albums a changé. Rechargez la page avant de réessayer.")
        else:
            messages.success(request, "Les albums ont été rangés dans cet album.")
            return redirect("library:album_detail", pk=album.pk)
    return render(request, "library/album_group.html", {"form": form, "album": album})


@login_required
@require_http_methods(["GET", "POST"])
def album_cover(request, pk):
    album = get_object_or_404(Album.objects.visible_to(request.user), pk=pk)
    covers = AlbumCovers(request.user)
    form = AlbumCoverForm(
        request.POST if request.method == "POST" else None,
        user=request.user, album=album, covers=covers,
    )
    if request.method == "POST" and form.is_valid():
        try:
            album = form.save()
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, "La vignette a été mise à jour." if album.cover_photo_id else "La vignette est à nouveau choisie automatiquement.")
            return redirect("library:album_detail", pk=album.pk)
    covers.apply([album])
    photos = form.fields["cover_photo"].queryset
    query = request.GET.get("q", "").strip()[:200]
    if query:
        photos = photos.filter(Q(title__icontains=query) | Q(original_name__icontains=query))
    page = Paginator(photos, 48).get_page(request.GET.get("page"))
    return render(request, "library/album_cover.html", {
        "album": album, "form": form, "page_obj": page,
        "query": query, "pagination_query": urlencode({"q": query}),
    })
