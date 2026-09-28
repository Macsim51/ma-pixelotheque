"""Album capabilities shared by views, forms and future upload endpoints."""


def _is_active(user):
    return bool(user.is_authenticated and user.is_active)


def can_create_album(user):
    return _is_active(user) and (user.is_superuser or user.role == "family")


def can_manage_album(user, album):
    return _is_active(user) and (
        user.is_superuser or (user.role == "family" and album.owner_id == user.pk)
    )


def can_upload_to_album(user, album):
    if not _is_active(user):
        return False
    if user.is_superuser:
        return True
    if user.role != "family":
        return False
    if album.owner_id == user.pk:
        return True
    if album.visibility == "family":
        return album.allow_family_uploads
    if album.visibility == "restricted":
        return album.permissions.filter(user_id=user.pk, can_upload=True).exists()
    return False


def can_edit_media(user, media, app_settings=None):
    from apps.core.models import AppSetting
    from .models import MediaItem

    if not _is_active(user):
        return False
    if not MediaItem.objects.visible_to(user).filter(pk=media.pk).exists():
        return False
    if user.is_superuser:
        return True
    configuration = app_settings or AppSetting.load()
    return user.role == "family" and media.uploader_id == user.pk and configuration.allow_family_edit
