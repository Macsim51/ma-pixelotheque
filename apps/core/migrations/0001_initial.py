import django.core.validators
from django.db import migrations, models


def initial_settings(apps, schema_editor):
    apps.get_model("core", "AppSetting").objects.get_or_create(pk=1)


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="AppSetting",
            fields=[
                ("id", models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ("auto_generate", models.BooleanField(default=True, verbose_name="générer les miniatures après l’upload")),
                ("allow_family_edit", models.BooleanField(default=True, verbose_name="autoriser chacun à modifier ses photos")),
                ("allow_family_sharing", models.BooleanField(default=True, verbose_name="autoriser le partage des albums par leur créateur")),
                ("thumbnail_size", models.PositiveIntegerField(default=480, validators=[django.core.validators.MinValueValidator(120), django.core.validators.MaxValueValidator(1024)], verbose_name="taille des miniatures (pixels)")),
                ("preview_size", models.PositiveIntegerField(default=2048, validators=[django.core.validators.MinValueValidator(480), django.core.validators.MaxValueValidator(4096)], verbose_name="taille des previews (pixels)")),
                ("preview_quality", models.PositiveSmallIntegerField(default=82, validators=[django.core.validators.MinValueValidator(40), django.core.validators.MaxValueValidator(95)], verbose_name="qualité des previews WebP")),
                ("max_upload_mb", models.PositiveSmallIntegerField(default=30, validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(100)], verbose_name="poids maximal par photo (Mio)")),
                ("max_image_pixels", models.PositiveIntegerField(default=40000000, validators=[django.core.validators.MinValueValidator(1000000), django.core.validators.MaxValueValidator(80000000)], verbose_name="nombre maximal de pixels")),
                ("worker_paused", models.BooleanField(default=False, verbose_name="mettre les traitements en pause")),
            ],
            options={"verbose_name": "réglages de l’instance", "verbose_name_plural": "réglages de l’instance", "constraints": [models.CheckConstraint(condition=models.Q(id=1), name="core_settings_singleton")]},
        ),
        migrations.RunPython(initial_settings, migrations.RunPython.noop),
    ]
