from django.urls import path

from apps.accounts import views

urlpatterns = [
    path("login", views.login_view, name="login"),
    path("logout", views.logout_view, name="logout"),
    path("login/email", views.magic_request_view, name="magic-request"),
    path("login/magic/<str:token>", views.magic_confirm_view, name="magic-confirm"),
]
