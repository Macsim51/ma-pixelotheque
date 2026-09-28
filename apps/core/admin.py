from django.contrib import admin

from .models import AppSetting


@admin.register(AppSetting)
class AppSettingAdmin(admin.ModelAdmin):
    fieldsets = (
        ("Vie de famille", {"fields": ("allow_family_edit", "allow_family_sharing")}),
        ("Réception des photos", {"fields": ("max_upload_mb", "max_image_pixels")}),
        ("Traitements", {"fields": ("auto_generate", "worker_paused", "concurrency")}),
        ("Images affichées", {"fields": ("thumbnail_size", "preview_size", "preview_quality")}),
    )
    readonly_fields = ("concurrency",)

    @admin.display(description="Traitements simultanés")
    def concurrency(self, obj):
        return "1 — plafond du MVP sur Raspberry Pi"

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not AppSetting.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
