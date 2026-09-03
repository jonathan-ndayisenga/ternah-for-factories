from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import include, path


@login_required
def home(request):
    """Route by who you are. Owner always has more than one place they might
    mean, so Home is always their tile picker. A Manager is the same, but
    only while she's in her own (MANAGER) view — Home there is her picker
    over Branch/Manage/Finance plus whatever views she's been granted; once
    she's switched into one of those views, Home just means that view's own
    landing page, same as it would for a native user of that role — getting
    back to her picker is "Back to Manager" (the mode banner, or the Home
    link inside that view's nav, which points at the same switch-to-MANAGER
    route), not a second meaning of Home. Everyone else has exactly one
    destination and is sent straight there, every time — a Cashier/Rep/
    Production user never sees a picker with one tile on it."""
    from accounts.views import manager_tiles, owner_tiles
    u = request.user
    if u.is_superuser:
        return redirect("platformadmin:dashboard")
    if u.role == "OWNER":
        return render(request, "home_tiles.html", {"tiles": owner_tiles()})
    if u.role == "MANAGER":
        if request.active_view in ("CASHIER", "SALES_REP"):
            return redirect("sales:pos")
        if request.active_view == "PRODUCTION":
            return redirect("production:dashboard")
        return render(request, "home_tiles.html", {"tiles": manager_tiles(u)})
    if u.role in ("CASHIER", "SALES_REP"):
        return redirect("sales:pos")
    if u.role == "PRODUCTION":
        return redirect("production:dashboard")
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
