import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name="Album",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=150, verbose_name="titre")),
                ("description", models.TextField(blank=True, max_length=2000, verbose_name="description")),
                ("visibility", models.CharField(choices=[("family", "Familial"), ("private", "Privé"), ("restricted", "Restreint")], db_index=True, default="family", max_length=10, verbose_name="visibilité")),
                ("allow_family_uploads", models.BooleanField(default=True, verbose_name="autoriser les contributions familiales")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="créé le")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="modifié le")),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="owned_albums", to=settings.AUTH_USER_MODEL, verbose_name="créateur")),
            ],
            options={"verbose_name": "album", "verbose_name_plural": "albums", "ordering": ("-created_at", "-id")},
        ),
        migrations.CreateModel(
            name="AlbumPermission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("can_upload", models.BooleanField(default=False, verbose_name="peut ajouter des photos")),
                ("album", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="permissions", to="library.album")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="album_permissions", to=settings.AUTH_USER_MODEL, verbose_name="utilisateur")),
            ],
            options={
                "verbose_name": "accès à un album",
                "verbose_name_plural": "accès aux albums",
                "constraints": [models.UniqueConstraint(fields=("album", "user"), name="unique_album_user_permission")],
            },
        ),
    ]
