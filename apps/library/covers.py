"""Album covers from visible branches, without loading every photo or N+1 queries."""

from collections import defaultdict

from django.db.models import Exists, OuterRef, Subquery
from django.utils.functional import cached_property

from .models import Album, AlbumMedia, MediaItem


class AlbumCovers:
    def __init__(self, user):
        self.user = user

    @cached_property
    def nodes(self):
        # Each candidate already belongs to the visible outer album. Only IDs
        # and dates are loaded, at most one photo per album, never all photos.
        latest = (
            MediaItem.objects.filter(albums=OuterRef("pk"), status=MediaItem.Status.READY)
            .exclude(thumbnail_key="").order_by("-sort_date", "-id")
        )
        albums = Album.objects.visible_to(self.user).order_by().annotate(
            latest_id=Subquery(latest.values("pk")[:1]),
            latest_date=Subquery(latest.values("sort_date")[:1]),
        ).values_list("pk", "parent_id", "latest_id", "latest_date")
        return {
            pk: (parent, (date, photo_id) if photo_id else None)
            for pk, parent, photo_id, date in albums
        }

    @cached_property
    def children(self):
        children = defaultdict(list)
        for pk, (parent, _) in self.nodes.items():
            children[parent].append(pk)
        return children

    def branch_ids(self, album_id):
        # A hidden intermediate album stops traversal, just like navigation.
        if album_id not in self.nodes:
            return set()
        branch = set()
        pending = [album_id]
        while pending:
            pk = pending.pop()
            if pk not in branch:
                branch.add(pk)
                pending.extend(self.children[pk])
        return branch

    def photos_for(self, album_id):
        membership = AlbumMedia.objects.filter(
            media_id=OuterRef("pk"), album_id__in=self.branch_ids(album_id),
        )
        return (
            MediaItem.objects.filter(status=MediaItem.Status.READY).exclude(thumbnail_key="")
            .alias(_cover_member=Exists(membership)).filter(_cover_member=True)
            .order_by("-sort_date", "-id")
        )

    def apply(self, albums):
        albums = list(albums)
        if not albums:
            return
        selected = {album.cover_photo_id for album in albums if album.cover_photo_id}
        memberships = defaultdict(set)
        if selected:
            for media_id, album_id in (
                AlbumMedia.objects.filter(
                    media_id__in=selected,
                    media__status=MediaItem.Status.READY,
                    album_id__in=Album.objects.visible_to(self.user).values("pk"),
                ).exclude(media__thumbnail_key="").values_list("media_id", "album_id")
            ):
                memberships[media_id].add(album_id)
        for album in albums:
            branch = self.branch_ids(album.pk)
            album.cover_is_custom = bool(memberships[album.cover_photo_id] & branch)
            if album.cover_is_custom:
                album.cover_id = album.cover_photo_id
            else:
                latest = max((self.nodes[pk][1] for pk in branch if self.nodes[pk][1]), default=None)
                album.cover_id = latest[1] if latest else None
