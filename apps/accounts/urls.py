from django.urls import path

from . import views


app_name = "accounts"
urlpatterns = [
    path("login/", views.AccountLoginView.as_view(), name="login"),
    path("logout/", views.AccountLogoutView.as_view(), name="logout"),
    path("account/", views.account, name="account"),
]
