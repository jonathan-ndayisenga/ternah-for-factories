from django.urls import path
from . import views

app_name = "sales"
urlpatterns = [
    path("pos/", views.pos, name="pos"),
    path("pos/sale/", views.record_sale, name="record_sale"),
    path("pos/expense/", views.record_expense, name="record_expense"),
    path("pos/payment/", views.record_payment, name="record_payment"),
]
