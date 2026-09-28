from django.urls import path

from . import media_views, views

app_name = "library"

urlpatterns = [
    path("albums/", views.album_list, name="album_list"),
    path("albums/new/", views.album_create, name="album_create"),
    path("albums/<uuid:pk>/", views.album_detail, name="album_detail"),
    path("albums/<uuid:pk>/edit/", views.album_edit, name="album_edit"),
    path("photos/", media_views.timeline, name="timeline"),
    path("photos/year/<int:year>/", media_views.timeline, name="timeline_year"),
    path("photos/year/<int:year>/<int:month>/", media_views.timeline, name="timeline_month"),
    path("photos/year/<int:year>/<int:month>/<int:day>/", media_views.timeline, name="timeline_day"),
    path("photos/<uuid:pk>/", media_views.media_detail, name="media_detail"),
    path("photos/<uuid:pk>/edit/", media_views.media_edit, name="media_edit"),
    path("photos/<uuid:pk>/favorite/", media_views.favorite_toggle, name="favorite_toggle"),
    path("files/<uuid:pk>/<str:variant>/", media_views.media_file, name="media_file"),
    path("favorites/", media_views.favorites, name="favorites"),
    path("search/", media_views.search, name="search"),
    path("map/", media_views.map_view, name="map"),
    path("map/data/", media_views.map_data, name="map_data"),
    path("map/photos/", media_views.map_photos, name="map_photos"),
]
