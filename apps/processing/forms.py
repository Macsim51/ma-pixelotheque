from django import forms
from django.db.models import Q

from apps.library.models import Album

from .upload_handlers import MAX_FILES


class MultiplePhotoInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultiplePhotoField(forms.FileField):
    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else [data]
        if len(files) > MAX_FILES:
            raise forms.ValidationError(f"Ajoutez au maximum {MAX_FILES} photos par envoi.")
        return [super(MultiplePhotoField, self).clean(item, initial) for item in files]


class UploadForm(forms.Form):
    album = forms.ModelChoiceField(label="Album existant", queryset=Album.objects.none(), required=False)
    new_album = forms.CharField(label="Ou créer un album", max_length=150, required=False)
    files = MultiplePhotoField(
        label="Photos", widget=MultiplePhotoInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
        help_text="JPEG, PNG ou WebP fixes. Les photos seront vérifiées avant d’être affichées.",
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        albums = Album.objects.visible_to(user)
        if not user.is_superuser:
            albums = albums.filter(
                Q(owner=user) | Q(visibility="family", allow_family_uploads=True)
                | Q(visibility="restricted", permissions__user=user, permissions__can_upload=True)
            ).distinct()
        self.fields["album"].queryset = albums

    def clean(self):
        data = super().clean()
        if bool(data.get("album")) == bool(data.get("new_album")):
            raise forms.ValidationError("Choisissez un album existant ou indiquez le nom d’un nouvel album.")
        return data
