"""Public URLs; original photo storage must never be mounted here."""

from django.contrib import admin
from django.urls import include, path

from apps.core import views

urlpatterns = [
    path("", views.home, name="home"),
    path("healthz", views.healthz, name="healthz"),
    path("manage/", views.manage, name="manage"),
    path("accounts/", include("apps.accounts.urls")),
    path("upload/", include("apps.processing.urls")),
    path("s/", include("apps.sharing.urls")),
    path("", include("apps.library.urls")),
    path("admin/", admin.site.urls),
]

handler400 = "apps.core.views.bad_request"
handler403 = "apps.core.views.permission_denied"
handler404 = "apps.core.views.page_not_found"
handler500 = "apps.core.views.server_error"
