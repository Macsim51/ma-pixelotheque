from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.forms.models import BaseInlineFormSet
from django.urls import reverse
from django.utils.html import format_html

from .models import Album, AlbumPermission, MediaItem


class SuperuserOnlyAdminMixin:
    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_delete_permission(self, request, obj=None):
        return self.has_module_permission(request)


class AlbumPermissionInlineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if self.instance.visibility == Album.Visibility.PRIVATE:
            for form in self.forms:
                if form.cleaned_data.get("user") and not form.cleaned_data.get("DELETE"):
                    raise ValidationError("Supprimez les membres pour rendre l’album privé.")


class AlbumPermissionInline(SuperuserOnlyAdminMixin, admin.TabularInline):
    model = AlbumPermission
    formset = AlbumPermissionInlineFormSet
    extra = 0

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "user":
            kwargs["queryset"] = get_user_model().objects.filter(is_active=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Album)
class AlbumAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("title", "parent", "owner", "visibility", "allow_family_uploads", "created_at")
    list_filter = ("visibility", "allow_family_uploads")
    search_fields = ("title", "owner__username")
    list_select_related = ("owner", "parent")
    inlines = (AlbumPermissionInline,)
    readonly_fields = ("id", "created_at", "updated_at", "cover_management")
    actions = ("regenerate_selected",)

    @admin.display(description="Vignette")
    def cover_management(self, obj):
        if obj is None or obj._state.adding:
            return "Enregistrez l’album avant de choisir sa vignette."
        return format_html('<a href="{}">Choisir la vignette</a>', reverse("library:album_cover", args=[obj.pk]))

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    @admin.action(description="Régénérer les aperçus des albums sélectionnés", permissions=["view"])
    def regenerate_selected(self, request, queryset):
        from apps.processing.services import regenerate_queryset

        count = regenerate_queryset(MediaItem.objects.filter(albums__in=queryset).distinct())
        self.message_user(request, f"{count} traitement(s) ajouté(s) à la file.")

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and (obj is None or obj.can_delete_safely())

    def get_readonly_fields(self, request, obj=None):
        if obj is not None:
            return (*self.readonly_fields, "owner")
        return self.readonly_fields

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "owner":
            kwargs["queryset"] = get_user_model().objects.filter(is_active=True).filter(
                Q(role="family") | Q(is_superuser=True)
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(AlbumPermission)
class AlbumPermissionAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("album", "user", "can_upload")
    list_filter = ("can_upload",)
    search_fields = ("album__title", "user__username")
    list_select_related = ("album", "user")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "user":
            kwargs["queryset"] = get_user_model().objects.filter(is_active=True)
        elif db_field.name == "album":
            kwargs["queryset"] = Album.objects.exclude(visibility=Album.Visibility.PRIVATE)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(MediaItem)
class MediaItemAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("__str__", "uploader", "status", "derivative_status", "sort_date")
    list_filter = ("status", "derivative_status")
    list_select_related = ("uploader",)
    search_fields = ("title", "original_name", "uploader__username")
    fields = ("edit_information", "title", "description", "uploader", "status", "derivative_status", "sort_date", "captured_at", "taken_at_override", "camera", "width", "height")
    readonly_fields = fields
    actions = ("regenerate_selected",)

    @admin.action(description="Régénérer les aperçus des photos sélectionnées", permissions=["view"])
    def regenerate_selected(self, request, queryset):
        from apps.processing.services import regenerate_queryset

        count = regenerate_queryset(queryset)
        self.message_user(request, f"{count} traitement(s) ajouté(s) à la file.")

    @admin.display(description="Informations de la photo")
    def edit_information(self, obj):
        return format_html('<a href="{}">Modifier les informations</a>', reverse("library:media_edit", args=[obj.pk]))

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # Metadata editing uses the regular photo form. File deletion needs its own service.
        return False
