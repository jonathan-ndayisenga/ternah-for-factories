from django.urls import path
from . import views

app_name = "reports"
urlpatterns = [
    path("", views.owner_dashboard, name="owner_dashboard"),
    path("print/", views.print_reports, name="print_reports"),
    path("production/", views.production_report, name="production_report"),
    path("production/<int:pk>/", views.production_batch_detail, name="production_batch_detail"),
    path("cards/", views.stats_cards, name="stats_cards"),
    path("charts/daily-sales/", views.chart_daily_sales, name="chart_daily_sales"),
    path("charts/sales-by-branch/", views.chart_sales_by_branch, name="chart_sales_by_branch"),
    path("charts/top-reps/", views.chart_top_reps, name="chart_top_reps"),
    path("charts/payment-split/", views.chart_payment_split, name="chart_payment_split"),
    path("charts/expenses/", views.chart_expenses_by_category, name="chart_expenses"),
    path("charts/monthly-gp/", views.chart_monthly_gp, name="chart_monthly_gp"),
]
