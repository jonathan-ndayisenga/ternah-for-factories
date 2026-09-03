from django.urls import path
from . import views

app_name = "sales"
urlpatterns = [
    path("pos/", views.pos, name="pos"),
    path("pos/received/", views.received_items, name="received_items"),
    path("pos/order-stock/", views.stock_request_create, name="stock_request_create"),
    path("pos/sale/", views.record_sale, name="record_sale"),
    path("pos/expense/", views.record_expense, name="record_expense"),
    path("pos/opening-balance/", views.record_opening_balance, name="record_opening_balance"),
    path("pos/payment/", views.record_payment, name="record_payment"),
    path("pos/outlet-transfers/", views.outlet_transfers, name="outlet_transfers"),
    path("outlet-transfers/<int:pk>/confirm/", views.outlet_transfer_confirm, name="outlet_transfer_confirm"),
    path("receipt/<int:pk>/", views.receipt, name="receipt"),
    path("payment-receipt/<int:pk>/", views.debtor_payment_receipt, name="debtor_payment_receipt"),
]
