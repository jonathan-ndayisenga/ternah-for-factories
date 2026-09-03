from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import include, path


@login_required
def home(request):
    """Route by who you are. Home is always a real landing page, for every
    role — even a Cashier/Sales Rep/Production user, who only has one place
    to go, still sees it as a tile they land on and click through, rather
    than being silently skipped past it. That keeps Home consistent: it's
    always in the nav, always shows what you have access to, never a
    special case some roles get and others don't.

    A Manager is the one role with two layers: while she's in her own
    (MANAGER) view, Home is her picker over Branch/Manage/Finance plus
    whatever views she's been granted — picking one of those already IS her
    "click a tile" moment, so it goes straight to that view's landing page,
    not a second, redundant one-tile picker. Once she's switched, Home just
    means that view's landing page too; getting back to her own picker is
    "Back to Manager" (the mode banner, or the Home link inside that view's
    nav, which points at the same switch-to-MANAGER route)."""
    from accounts.views import manager_tiles, owner_tiles, single_role_tile
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
    if u.role in ("CASHIER", "SALES_REP", "PRODUCTION"):
        return render(request, "home_tiles.html", {"tiles": single_role_tile(u.role)})
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
