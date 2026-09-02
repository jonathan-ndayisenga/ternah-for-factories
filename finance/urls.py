from django.urls import path
from . import views

app_name = "finance"
urlpatterns = [
    path("cashbook/", views.cashbook, name="cashbook"),
    path("journal/", views.journal, name="journal"),
    path("general-ledger/", views.general_ledger, name="general_ledger"),
    path("expense-journal/", views.expense_journal, name="expense_journal"),
    path("reports/", views.financial_reports, name="financial_reports"),
    path("reports/capital/", views.record_capital, name="record_capital"),
    path("payment-accounts/", views.payment_accounts, name="payment_accounts"),
    path("payment-accounts/<str:kind>/<int:pk>/toggle/", views.payment_account_toggle, name="payment_account_toggle"),
]
