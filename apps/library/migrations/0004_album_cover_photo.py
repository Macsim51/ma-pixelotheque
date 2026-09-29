from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("library", "0003_album_parent")]

    operations = [
        migrations.AddField(
            model_name="album",
            name="cover_photo",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="album_covers",
                to="library.mediaitem",
                verbose_name="photo de vignette",
            ),
        ),
    ]
