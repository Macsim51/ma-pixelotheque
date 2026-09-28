from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction

from .models import Album, AlbumPermission, MediaItem, Tag
from .permissions import can_create_album, can_manage_album, can_edit_media


class UsernameMultipleChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, user):
        # Invitation choices never include email addresses or other profile data.
        return user.get_username()


class AlbumForm(forms.ModelForm):
    allowed_users = UsernameMultipleChoiceField(
        label="Membres autorisés",
        queryset=get_user_model().objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=(
            "Un album familial est déjà visible par toute la famille. "
            "Choisissez ici les invités ou les membres d’un album restreint. "
            "Un album privé ne peut pas avoir de membres."
        ),
    )
    contributing_users = UsernameMultipleChoiceField(
        label="Membres pouvant ajouter des photos",
        queryset=get_user_model().objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=(
            "Pour un album restreint, choisissez parmi les membres autorisés. "
            "Les comptes invités ne peuvent pas ajouter de photos."
        ),
    )

    class Meta:
        model = Album
        fields = (
            "title",
            "description",
            "visibility",
            "allow_family_uploads",
            "allowed_users",
            "contributing_users",
        )
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "title": forms.TextInput(attrs={"autocomplete": "off"}),
        }
        labels = {
            "allow_family_uploads": "Autoriser la famille à ajouter des photos",
        }
        help_texts = {
            "visibility": (
                "Familial : toute la famille et les invités choisis. "
                "Privé : vous et les administrateurs. "
                "Restreint : vous, les administrateurs et les membres choisis."
            ),
            "allow_family_uploads": "Cette option concerne les albums familiaux.",
        }

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = user
        if self.instance._state.adding:
            if not can_create_album(user):
                raise PermissionDenied
            self.instance.owner = user
        elif not can_manage_album(user, self.instance):
            raise PermissionDenied

        candidates = (
            get_user_model().objects.filter(is_active=True)
            .exclude(pk=self.instance.owner_id)
            .order_by("username")
        )
        self.fields["allowed_users"].queryset = candidates
        self.fields["contributing_users"].queryset = candidates.filter(role="family")
        if not self.instance._state.adding:
            memberships = list(
                self.instance.permissions.values_list("user_id", "can_upload")
            )
            self.initial["allowed_users"] = [user_id for user_id, _ in memberships]
            self.initial["contributing_users"] = [
                user_id for user_id, contributes in memberships if contributes
            ]

    def clean(self):
        cleaned = super().clean()
        allowed = cleaned.get("allowed_users")
        contributors = cleaned.get("contributing_users")
        if cleaned.get("visibility") == Album.Visibility.PRIVATE:
            if allowed:
                self.add_error("allowed_users", "Retirez les membres pour rendre l’album privé.")
            if contributors:
                self.add_error(
                    "contributing_users", "Un album privé ne peut pas avoir de contributeurs."
                )
        if allowed is not None and contributors is not None:
            member_ids = {user.pk for user in allowed}
            if any(user.pk not in member_ids for user in contributors):
                self.add_error(
                    "contributing_users", "Chaque contributeur doit être un membre autorisé."
                )
        return cleaned

    def _save_m2m(self):
        super()._save_m2m()
        member_ids = {user.pk for user in self.cleaned_data["allowed_users"]}
        contributor_ids = {user.pk for user in self.cleaned_data["contributing_users"]}
        self.instance.permissions.exclude(user_id__in=member_ids).delete()
        for user_id in member_ids:
            AlbumPermission.objects.update_or_create(
                album=self.instance,
                user_id=user_id,
                defaults={"can_upload": user_id in contributor_ids},
            )

    @transaction.atomic
    def save(self, commit=True):
        """Persist visibility and membership together, rechecking management rights."""
        if self.instance._state.adding:
            if not can_create_album(self.actor):
                raise PermissionDenied
            self.instance.owner = self.actor
        else:
            current = Album.objects.get(pk=self.instance.pk)
            if not can_manage_album(self.actor, current):
                raise PermissionDenied
            # The owner is immutable through this form, including crafted requests.
            self.instance.owner_id = current.owner_id
        return super().save(commit=commit)


class MediaItemForm(forms.ModelForm):
    albums = forms.ModelMultipleChoiceField(
        label="Albums", queryset=Album.objects.none(), required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=(
            "Choisissez les albums dans lesquels vous pouvez ajouter des photos. "
            "Les associations aux autres albums restent inchangées. "
            "Une photo doit rester dans au moins un album ; chaque album peut élargir son audience."
        ),
    )
    tags_input = forms.CharField(
        label="Tags", required=False, max_length=2000,
        help_text="Séparez les tags par des virgules, jusqu’à 20 tags de 100 caractères.",
    )

    class Meta:
        model = MediaItem
        fields = ("title", "description", "taken_at_override", "tags_input", "albums")
        widgets = {
            "description": forms.Textarea(attrs={"rows": 5}),
            "taken_at_override": forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}),
        }
        help_texts = {"taken_at_override": "Laissez vide pour conserver la date EXIF, ou la date d’ajout si elle est absente."}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = user
        if not can_edit_media(user, self.instance):
            raise PermissionDenied
        writable = Album.objects.uploadable_to(user)
        self.fields["albums"].queryset = writable
        self.initial["albums"] = list(self.instance.albums.filter(pk__in=writable.values("pk")).values_list("pk", flat=True))
        self.initial["tags_input"] = ", ".join(self.instance.tags.values_list("name", flat=True))

    def clean(self):
        cleaned = super().clean()
        selected = cleaned.get("albums")
        if selected is not None and not selected:
            retained = self.instance.albums.exclude(pk__in=self.fields["albums"].queryset.values("pk"))
            if not retained.exists():
                self.add_error("albums", "Conservez au moins un album pour cette photo.")
        return cleaned

    def clean_tags_input(self):
        names = {}
        for raw in self.cleaned_data["tags_input"].split(","):
            name = " ".join(raw.split())
            normalized = Tag.normalize(name)
            if not normalized:
                continue
            if len(name) > 100 or len(normalized) > 100:
                raise forms.ValidationError("Chaque tag doit contenir au maximum 100 caractères.")
            names.setdefault(normalized, name)
        if len(names) > 20:
            raise forms.ValidationError("Une photo peut avoir au maximum 20 tags.")
        return names

    def clean_taken_at_override(self):
        value = self.cleaned_data["taken_at_override"]
        if value is not None and not 2 <= value.year <= 9998:
            raise forms.ValidationError("Choisissez une année comprise entre 2 et 9998.")
        return value

    def _save_m2m(self):
        # Do not call ModelForm's automatic albums.set(): non-writable and
        # hidden existing associations must survive this actor's changes.
        actor = get_user_model().objects.get(pk=self.actor.pk)
        current = MediaItem.objects.get(pk=self.instance.pk)
        if not can_edit_media(actor, current):
            raise PermissionDenied
        selected_ids = {album.pk for album in self.cleaned_data["albums"]}
        writable = Album.objects.uploadable_to(actor)
        if writable.filter(pk__in=selected_ids).count() != len(selected_ids):
            raise PermissionDenied
        retained_ids = set(current.albums.exclude(pk__in=writable.values("pk")).values_list("pk", flat=True))
        destination_ids = selected_ids | retained_ids
        if not destination_ids:
            raise forms.ValidationError("Une photo doit rester dans au moins un album.")
        current.albums.set(destination_ids)
        tags = [
            Tag.objects.get_or_create(normalized=normalized, defaults={"name": name})[0]
            for normalized, name in self.cleaned_data["tags_input"].items()
        ]
        self.instance.tags.set(tags)

    @transaction.atomic
    def save(self, commit=True):
        current = MediaItem.objects.get(pk=self.instance.pk)
        self.actor = get_user_model().objects.get(pk=self.actor.pk)
        if not can_edit_media(self.actor, current):
            raise PermissionDenied
        for field in ("title", "description", "taken_at_override"):
            setattr(current, field, self.cleaned_data[field])
        self.instance = current
        instance = super().save(commit=False)
        if commit:
            instance.save(update_fields=["title", "description", "taken_at_override", "sort_date"])
            self._save_m2m()
        return instance


class SearchForm(forms.Form):
    q = forms.CharField(label="Rechercher", required=False, max_length=100,
                        widget=forms.TextInput(attrs={"placeholder": "Titre, album, description ou tag"}))
    date_from = forms.DateField(label="Du", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(label="Au", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    uploader = forms.ModelChoiceField(label="Ajouté par", queryset=get_user_model().objects.none(), required=False)
    camera = forms.CharField(label="Appareil photo", required=False, max_length=200)
    gps = forms.ChoiceField(label="Localisation", required=False,
                           choices=(("", "Toutes les photos"), ("yes", "Avec GPS"), ("no", "Sans GPS")))
    favorites = forms.BooleanField(label="Mes favoris uniquement", required=False)

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["uploader"].queryset = get_user_model().objects.filter(
            pk__in=MediaItem.objects.visible_to(user).filter(status=MediaItem.Status.READY).values("uploader_id")
        ).order_by("username")

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("date_from"), cleaned.get("date_to")
        if start and end and start > end:
            raise forms.ValidationError("La date de début doit précéder la date de fin.")
        for field in ("date_from", "date_to"):
            value = cleaned.get(field)
            if value and not 2 <= value.year <= 9998:
                self.add_error(field, "Choisissez une année comprise entre 2 et 9998.")
        return cleaned
