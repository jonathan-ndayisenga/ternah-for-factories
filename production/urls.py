from django.urls import path
from . import views

app_name = "production"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("products/", views.product_list, name="product_list"),
    path("products/new/", views.product_create, name="product_create"),
    path("products/<int:pk>/price/", views.product_price_edit, name="product_price_edit"),
    path("products/<int:pk>/cost-history/", views.product_cost_history, name="product_cost_history"),
    path("products/<int:product_pk>/formula/", views.formula_edit, name="formula_edit"),
    path("distributions/", views.distribution_list, name="distributions"),
    path("distributions/new/", views.distribution_create, name="distribution_create"),
    path("distributions/<int:pk>/confirm/", views.distribution_confirm, name="distribution_confirm"),
    path("categories/", views.category_list, name="categories"),
    path("raw-materials/", views.raw_material_list, name="raw_materials"),
    path("raw-materials/new/", views.raw_material_create, name="raw_material_create"),
    path("raw-materials/<int:pk>/edit/", views.raw_material_edit, name="raw_material_edit"),
    path("raw-materials/<int:pk>/toggle-active/", views.raw_material_toggle_active, name="raw_material_toggle_active"),
    path("raw-materials/<int:pk>/purchase/", views.raw_material_purchase, name="raw_material_purchase"),
    path("raw-materials/<int:pk>/movements/", views.raw_material_movements, name="raw_material_movements"),
    path("batches/new/", views.batch_create, name="batch_create"),
    path("batches/processing/", views.processing_list, name="processing"),
    path("batches/<int:pk>/complete/", views.batch_complete, name="batch_complete"),
    path("stock-requests/", views.stock_request_list, name="stock_requests"),
    path("stock-requests/<int:pk>/fulfill/", views.stock_request_fulfill, name="stock_request_fulfill"),
]
