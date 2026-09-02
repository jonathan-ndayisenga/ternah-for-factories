from django.urls import path
from . import views

app_name = "manager"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("debtors/", views.debtors, name="debtors"),
    path("debtors/<int:pk>/", views.debtor_detail, name="debtor_detail"),
    path("approvals/", views.approvals, name="approvals"),
    path("approvals/<int:pk>/decide/", views.approval_decide, name="approval_decide"),
    path("inventory/", views.inventory, name="inventory"),
]
