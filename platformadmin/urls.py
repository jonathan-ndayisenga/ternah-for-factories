from django.urls import path
from . import views

app_name = "platformadmin"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("onboard/", views.onboard, name="onboard"),
    path("business/<int:pk>/", views.business_detail, name="business_detail"),
    path("business/<int:pk>/extend/", views.extend_subscription, name="extend_subscription"),
    path("business/<int:pk>/toggle/", views.toggle_active, name="toggle_active"),
    path("business/<int:pk>/module/<int:module_pk>/toggle/", views.toggle_module, name="toggle_module"),
]
