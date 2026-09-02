from django.urls import path
from . import views

app_name = "finance"
urlpatterns = [
    path("cashbook/", views.cashbook, name="cashbook"),
    path("journal/", views.journal, name="journal"),
    path("payment-accounts/", views.payment_accounts, name="payment_accounts"),
    path("payment-accounts/<str:kind>/<int:pk>/toggle/", views.payment_account_toggle, name="payment_account_toggle"),
]
