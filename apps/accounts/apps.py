from django.apps import AppConfig
from django.contrib.admin import apps as admin_apps


class AccountsConfig(AppConfig):
    default = True
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    verbose_name = "Comptes"


class PixelothequeAdminConfig(admin_apps.AdminConfig):
    """Keep Django's default admin registry while restricting its site."""

    default = False
    default_site = "apps.accounts.admin_site.SuperuserAdminSite"
