def active_view(request):
    user = getattr(request, "user", None)
    return {
        "active_view": getattr(request, "active_view", None),
        "switchable_views": user.switchable_views() if user and user.is_authenticated else [],
        "owner_section": getattr(request, "owner_section", None),
        "manager_section": getattr(request, "manager_section", None),
    }


def notifications(request):
    """Badge counts shown in the nav / home tiles — cheap enough (one small
    count query, only for roles that can actually act on it) to compute on
    every page rather than push through session state that could go stale."""
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated) or user.is_superuser:
        return {"pending_stock_requests": 0}
    has_production_access = user.role in ("OWNER", "PRODUCTION") or (
        user.role == "MANAGER" and user.modules.filter(code="PRODUCTION").exists())
    if not has_production_access:
        return {"pending_stock_requests": 0}
    from sales.models import StockRequest
    count = StockRequest.objects.filter(business=user.business, status__in=("SUBMITTED", "PARTIAL")).count()
    return {"pending_stock_requests": count}
