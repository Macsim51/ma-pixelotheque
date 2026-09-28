from django import forms
from django.utils import timezone


class SharedLinkForm(forms.Form):
    expires_at = forms.DateTimeField(
        label="Date d’expiration", required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        help_text="Facultative. Le lien peut être désactivé ou révoqué à tout moment.",
    )
    allow_download = forms.BooleanField(
        label="Autoriser le téléchargement des originaux", required=False,
        help_text="Les originaux peuvent contenir les coordonnées GPS de prise de vue.",
    )

    def clean_expires_at(self):
        expires_at = self.cleaned_data["expires_at"]
        if expires_at is not None and expires_at <= timezone.now():
            raise forms.ValidationError("Choisissez une date dans le futur.")
        return expires_at
