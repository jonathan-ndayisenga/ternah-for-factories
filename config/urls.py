from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import include, path


@login_required
def home(request):
    """Route by who you are — super user to the platform console, owner to
    the business-wide dashboard, manager to their branch's dashboard (or
    wherever they've switched to via the view switcher), cashiers/reps to
    the POS, everyone else to a coming-soon stub."""
    u = request.user
    if u.is_superuser:
        return redirect("platformadmin:dashboard")
    if u.role == "OWNER":
        return redirect("reports:owner_dashboard")
    view = request.active_view if u.role == "MANAGER" else u.role
    if view == "MANAGER":
        return redirect("manager:dashboard")
    if view in ("CASHIER", "SALES_REP"):
        return redirect("sales:pos")
    if view == "PRODUCTION":
        return redirect("production:distributions")
    return redirect("coming_soon")


@login_required
def coming_soon(request):
    from accounts.views import ROLE_LABELS
    view = request.active_view if request.user.role == "MANAGER" else request.user.role
    return render(request, "coming_soon.html", {"acting_role_label": ROLE_LABELS.get(view, view)})


urlpatterns = [
    path("", home, name="home"),
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("platform/", include("platformadmin.urls")),
    path("reports/", include("reports.urls")),
    path("branches/", include("core.urls")),
    path("sales/", include("sales.urls")),
    path("production/", include("production.urls")),
    path("manager/", include("manager.urls")),
    path("finance/", include("finance.urls")),
    path("coming-soon/", coming_soon, name="coming_soon"),
]
