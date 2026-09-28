from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_safe

from .forms import AlbumForm
from .models import Album, MediaItem
from .permissions import can_create_album, can_manage_album


@login_required
@require_safe
def album_list(request):
    media = MediaItem.objects.visible_to(request.user).filter(albums=OuterRef("pk"))
    cover = media.filter(status=MediaItem.Status.READY).exclude(thumbnail_key="").order_by("-sort_date", "-id")
    counts = media.order_by().values("albums").annotate(total=Count("pk")).values("total")
    albums = Album.objects.visible_to(request.user).select_related("owner").annotate(
        cover_id=Subquery(cover.values("pk")[:1]),
        media_count=Coalesce(Subquery(counts[:1]), Value(0)),
    )
    page = Paginator(albums, 24).get_page(request.GET.get("page"))
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
    context.update({
        "album": album,
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
    form = AlbumForm(request.POST if request.method == "POST" else None, user=request.user)
    if request.method == "POST" and form.is_valid():
        album = form.save()
        messages.success(request, "L’album a été créé.")
        return redirect("library:album_detail", pk=album.pk)
    return render(request, "library/album_form.html", {"form": form, "is_creation": True})


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
        album = form.save()
        messages.success(request, "Les modifications ont été enregistrées.")
        return redirect("library:album_detail", pk=album.pk)
    return render(
        request,
        "library/album_form.html",
        {"form": form, "album": album, "is_creation": False},
    )
