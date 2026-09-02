from django.urls import path
from . import views

app_name = "production"
urlpatterns = [
    path("products/", views.product_list, name="product_list"),
    path("products/<int:pk>/price/", views.product_price_edit, name="product_price_edit"),
    path("distributions/", views.distribution_list, name="distributions"),
    path("distributions/new/", views.distribution_create, name="distribution_create"),
    path("distributions/<int:pk>/confirm/", views.distribution_confirm, name="distribution_confirm"),
]
