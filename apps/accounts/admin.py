from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import LoginRateLimit, User


@admin.register(User)
class PixelothequeUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("Pixelothèque", {"fields": ("role", "theme")}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("Pixelothèque", {"fields": ("role", "theme")}),)
    list_display = ("username", "role", "is_superuser", "is_active")
    list_filter = ("role", "is_superuser", "is_active")


@admin.register(LoginRateLimit)
class LoginRateLimitAdmin(admin.ModelAdmin):
    list_display = ("key", "attempts", "expires_at")
    readonly_fields = ("key", "attempts", "expires_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
