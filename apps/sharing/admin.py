from django.contrib import admin
from django.utils import timezone

from .models import SharedLink


@admin.register(SharedLink)
class SharedLinkAdmin(admin.ModelAdmin):
    list_display = ("id", "album", "creator", "active", "expires_at", "revoked_at", "access_count")
    list_filter = ("active", "allow_download")
    readonly_fields = ("album", "creator", "active", "expires_at", "allow_download", "created_at", "last_accessed_at", "access_count", "revoked_at")
    fields = readonly_fields
    actions = ("revoke_links",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Révoquer définitivement les liens sélectionnés")
    def revoke_links(self, request, queryset):
        queryset.filter(revoked_at__isnull=True).update(active=False, revoked_at=timezone.now())
