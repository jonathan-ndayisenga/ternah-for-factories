from django.urls import path
from . import views

app_name = "production"
urlpatterns = [
    path("products/", views.product_list, name="product_list"),
    path("products/new/", views.product_create, name="product_create"),
    path("products/<int:pk>/price/", views.product_price_edit, name="product_price_edit"),
    path("products/<int:product_pk>/formula/", views.formula_edit, name="formula_edit"),
    path("distributions/", views.distribution_list, name="distributions"),
    path("distributions/new/", views.distribution_create, name="distribution_create"),
    path("distributions/<int:pk>/confirm/", views.distribution_confirm, name="distribution_confirm"),
    path("categories/", views.category_list, name="categories"),
    path("raw-materials/", views.raw_material_list, name="raw_materials"),
    path("raw-materials/new/", views.raw_material_create, name="raw_material_create"),
    path("raw-materials/<int:pk>/purchase/", views.raw_material_purchase, name="raw_material_purchase"),
    path("raw-materials/<int:pk>/movements/", views.raw_material_movements, name="raw_material_movements"),
]
