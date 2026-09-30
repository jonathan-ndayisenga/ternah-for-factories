from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import include, path

from manager import views as manager_views


@login_required
def home(request):
    """Route by who you are. A single-module login has exactly one place to
    be — Cashier/Sales Rep/Production go straight there, same as a Manager
    who's switched into one of those views already does. Showing a
    one-tile picker first would just be a wasted click every login.

    Home stays a real landing page only for roles with more than one place
    to go: Owner (Reports/Finance/Manage) and a Manager in her own
    (MANAGER) view (Branch/Manage/Finance plus whatever views she's been
    granted) — picking one of those already IS her "click a tile" moment,
    so it goes straight to that view's landing page, not a second picker.
    Getting back to that picker from within a module is the module's own
    "← Home" nav link, or the module switcher available from any page."""
    from accounts.views import owner_tiles
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
        return manager_views.home(request)
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
    path("home/tiles/<str:tile>/", manager_views.home_tile, name="home_tile"),
    path("home/search/", manager_views.home_search, name="home_search"),
    path("home/search-results/", manager_views.home_search_page, name="home_search_page"),
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
