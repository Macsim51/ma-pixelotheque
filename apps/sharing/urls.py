from django.urls import path, re_path

from . import views


app_name = "sharing"
urlpatterns = [
    path("manage/<uuid:album_id>/", views.manage, name="manage"),
    path("manage/<uuid:album_id>/<int:link_id>/<str:action>/", views.change_link, name="change_link"),
    path("<str:token>/", views.shared_album, name="album"),
    path("<str:token>/photos/<uuid:media_id>/", views.shared_photo, name="photo"),
    path("<str:token>/files/<uuid:media_id>/<str:variant>/", views.shared_file, name="file"),
    # Unknown paths and malformed UUIDs never fall back to the global shell.
    re_path(r"^.*$", views.unavailable, name="unavailable"),
]
