from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy
from . import views

urlpatterns = [
    path("login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(next_page="login"), name="logout"),
    path("switch/<str:view>/", views.switch_view, name="switch_view"),
    path("section/<str:key>/", views.select_section, name="select_section"),
    path("users/", views.user_list, name="user_list"),
    path("users/new/", views.user_create, name="user_create"),
    path("users/<int:pk>/edit/", views.user_edit, name="user_edit"),
    path("password/", auth_views.PasswordChangeView.as_view(
        template_name="registration/password_change.html",
        success_url=reverse_lazy("password_change_done"),
    ), name="password_change"),
    path("password/done/", auth_views.PasswordChangeDoneView.as_view(
        template_name="registration/password_change_done.html",
    ), name="password_change_done"),
]
