from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("library", "0001_initial"),
    ]
    operations = [
        migrations.CreateModel(
            name="SharedLink",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token_digest", models.CharField(editable=False, max_length=64, unique=True)),
                ("active", models.BooleanField(default=True, verbose_name="actif")),
                ("expires_at", models.DateTimeField(blank=True, null=True, verbose_name="expiration")),
                ("allow_download", models.BooleanField(default=False, verbose_name="téléchargement des originaux")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="création")),
                ("last_accessed_at", models.DateTimeField(blank=True, null=True, verbose_name="dernière ouverture")),
                ("access_count", models.PositiveBigIntegerField(default=0, verbose_name="ouvertures")),
                ("revoked_at", models.DateTimeField(blank=True, editable=False, null=True, verbose_name="révocation définitive")),
                ("album", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="shared_links", to="library.album")),
                ("creator", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="created_shared_links", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "lien privé", "verbose_name_plural": "liens privés",
                "ordering": ("-created_at", "-pk"),
                "constraints": [models.CheckConstraint(condition=models.Q(revoked_at__isnull=True) | models.Q(active=False), name="sharing_revoked_link_inactive")],
            },
        ),
    ]
