from django import forms
from django.contrib.auth.forms import AuthenticationForm, UsernameField

from .models import User


class LoginForm(AuthenticationForm):
    username = UsernameField(
        label="Nom d’utilisateur", max_length=150,
        widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "username"}),
    )


class PreferencesForm(forms.ModelForm):
    """An explicit allowlist prevents role and privilege changes via POST."""

    class Meta:
        model = User
        fields = ("theme",)
