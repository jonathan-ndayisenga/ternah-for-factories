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
    empty = {"pending_stock_requests": 0, "pending_deliveries": 0}
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated) or user.is_superuser:
        return empty

    pending_stock_requests = 0
    has_production_access = user.role in ("OWNER", "PRODUCTION") or (
        user.role == "MANAGER" and user.modules.filter(code="PRODUCTION").exists())
    if has_production_access:
        from sales.models import StockRequest
        pending_stock_requests = StockRequest.objects.filter(
            business=user.business, status__in=("SUBMITTED", "PARTIAL")).count()

    # a rep's own deliveries, sitting at "Sent" until they confirm them
    # themselves — nobody else reliably sees these to confirm on their behalf
    pending_deliveries = 0
    acting_role = request.active_view if user.role == "MANAGER" else user.role
    if acting_role == "SALES_REP":
        location = getattr(user, "inventory", None)
        if location:
            from production.models import Distribution
            pending_deliveries = Distribution.objects.filter(receiver_location=location, status="SENT").count()

    return {"pending_stock_requests": pending_stock_requests, "pending_deliveries": pending_deliveries}
