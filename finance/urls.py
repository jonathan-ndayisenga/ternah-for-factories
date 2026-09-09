from django.urls import path
from . import views

app_name = "finance"
urlpatterns = [
    path("cashbook/", views.cashbook, name="cashbook"),
    path("journal/", views.journal, name="journal"),
    path("general-ledger/", views.general_ledger, name="general_ledger"),
    path("general-ledger/post/", views.manual_journal_entry, name="manual_journal_entry"),
    path("expense-journal/", views.expense_journal, name="expense_journal"),
    path("expense-journal/new/", views.expense_create, name="expense_create"),
    path("reports/", views.financial_reports, name="financial_reports"),
    path("reports/capital/", views.record_capital, name="record_capital"),
    path("payment-accounts/", views.payment_accounts, name="payment_accounts"),
    path("payment-accounts/<str:kind>/<int:pk>/toggle/", views.payment_account_toggle, name="payment_account_toggle"),
    path("invoices/", views.invoices, name="invoices"),
    path("invoices/new/", views.invoice_create, name="invoice_create"),
    path("invoices/<int:pk>/", views.invoice_detail, name="invoice_detail"),
    path("invoices/<int:pk>/payment/", views.invoice_payment, name="invoice_payment"),
    path("invoices/<int:pk>/void/", views.invoice_void, name="invoice_void"),
    path("invoice-clients/", views.invoice_clients, name="invoice_clients"),
    path("invoice-clients/<int:pk>/toggle-active/", views.invoice_client_toggle_active, name="invoice_client_toggle_active"),
]
