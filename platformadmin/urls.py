from django.urls import path
from . import views

app_name = "platformadmin"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("onboard/", views.onboard, name="onboard"),
    path("business/<int:pk>/top-up/", views.top_up, name="top_up"),
    path("business/<int:pk>/toggle/", views.toggle_active, name="toggle_active"),
]
