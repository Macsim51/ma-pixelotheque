from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("library", "0002_media")]

    operations = [
        migrations.AddField(
            model_name="album",
            name="parent",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="children",
                to="library.album",
                verbose_name="album parent",
            ),
        ),
        migrations.AddConstraint(
            model_name="album",
            constraint=models.CheckConstraint(
                condition=~models.Q(parent=models.F("id")),
                name="album_not_own_parent",
            ),
        ),
    ]
