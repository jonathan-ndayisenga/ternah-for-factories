from django.urls import path
from . import views

app_name = "core"
urlpatterns = [
    path("", views.branch_list, name="branches"),
    path("new/", views.branch_create, name="branch_create"),
    path("<int:pk>/edit/", views.branch_edit, name="branch_edit"),
    path("<int:pk>/toggle-active/", views.branch_toggle_active, name="branch_toggle_active"),
    path("settings/", views.business_settings, name="business_settings"),
]
