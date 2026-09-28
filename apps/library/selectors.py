"""Permission-filtered, bounded selectors for photo navigation."""

from datetime import datetime, time, timedelta
import math

from django.core.exceptions import ValidationError
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from .models import Album, AlbumMedia, Favorite, MediaItem, Tag


def local_midnight(day):
    return timezone.make_aware(datetime.combine(day, time.min), timezone.get_current_timezone())


def ready_media(user):
    return MediaItem.objects.visible_to(user).filter(status=MediaItem.Status.READY)


def apply_search(queryset, user, cleaned):
    text = cleaned.get("q")
    if text:
        matching_albums = AlbumMedia.objects.filter(
            media_id=OuterRef("pk"), album_id__in=Album.objects.visible_to(user).filter(title__icontains=text).values("pk")
        )
        matching_tags = MediaItem.tags.through.objects.filter(
            mediaitem_id=OuterRef("pk"), tag_id__in=Tag.objects.filter(normalized__contains=Tag.normalize(text)).values("pk")
        )
        queryset = queryset.alias(_album_match=Exists(matching_albums), _tag_match=Exists(matching_tags)).filter(
            Q(title__icontains=text) | Q(description__icontains=text) | Q(_album_match=True) | Q(_tag_match=True)
        )
    if cleaned.get("date_from"):
        queryset = queryset.filter(sort_date__gte=local_midnight(cleaned["date_from"]))
    if cleaned.get("date_to"):
        queryset = queryset.filter(sort_date__lt=local_midnight(cleaned["date_to"] + timedelta(days=1)))
    if cleaned.get("uploader"):
        queryset = queryset.filter(uploader=cleaned["uploader"])
    if cleaned.get("camera"):
        queryset = queryset.filter(camera__icontains=cleaned["camera"])
    if cleaned.get("gps") == "yes":
        queryset = queryset.filter(latitude__isnull=False, longitude__isnull=False)
    elif cleaned.get("gps") == "no":
        queryset = queryset.filter(latitude__isnull=True, longitude__isnull=True)
    if cleaned.get("favorites"):
        queryset = favorite_media(queryset, user)
    return queryset


def favorite_media(queryset, user):
    return queryset.alias(_favorite_access=Exists(
        Favorite.objects.filter(media_id=OuterRef("pk"), user_id=user.pk)
    )).filter(_favorite_access=True)


def parse_bbox(value):
    try:
        west, south, east, north = (float(part) for part in value.split(","))
    except (ValueError, AttributeError):
        raise ValidationError("La zone géographique doit contenir quatre coordonnées.") from None
    if not all(math.isfinite(part) for part in (west, south, east, north)):
        raise ValidationError("Coordonnées non finies.")
    if not (-180 <= west <= 180 and -180 <= east <= 180 and -90 <= south < north <= 90) or west == east:
        raise ValidationError("Zone géographique invalide.")
    span = east - west if east > west else east + 360 - west
    if span < 1e-9 or north - south < 1e-9:
        raise ValidationError("Zone géographique trop étroite.")
    return west, south, east, north


def in_bbox(queryset, bounds):
    west, south, east, north = bounds
    queryset = queryset.filter(latitude__gte=south, latitude__lte=north)
    if west > east:
        return queryset.filter(Q(longitude__gte=west) | Q(longitude__lte=east))
    return queryset.filter(longitude__gte=west, longitude__lte=east)
