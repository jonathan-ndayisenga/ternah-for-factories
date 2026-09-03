from django.urls import path
from . import views

app_name = "core"
urlpatterns = [
    path("", views.branch_list, name="branches"),
    path("new/", views.branch_create, name="branch_create"),
    path("settings/", views.business_settings, name="business_settings"),
]
