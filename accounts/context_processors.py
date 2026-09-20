def active_view(request):
    user = getattr(request, "user", None)
    return {
        "active_view": getattr(request, "active_view", None),
        "switchable_views": user.switchable_views() if user and user.is_authenticated else [],
        "owner_section": getattr(request, "owner_section", None),
        "manager_section": getattr(request, "manager_section", None),
    }


def notifications(request):
    """Badge counts shown in the nav / home tiles — cheap enough (small
    count queries, only for roles that can actually act on it) to compute on
    every page rather than push through session state that could go stale.
    pending_notifications is the total behind the standalone Notifications
    nav item (see accounts.views.notifications for the full feed it summarizes);
    the others feed specific tiles/nav links so a badge always points at
    exactly where the thing needing attention lives."""
    empty = {"pending_stock_requests": 0, "pending_deliveries": 0, "pending_inventory": 0,
             "pending_approvals": 0, "pending_branch": 0, "pending_notifications": 0,
             "pending_incoming_returns": 0}
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

    # a manager's own branch: anything sitting in Inventory awaiting
    # confirmation, and anything sitting in Approvals awaiting a decision —
    # split so each badge can sit on the exact nav link it belongs to, and
    # summed for the Branch tile on Home (see manager_tiles' own description:
    # "Dashboard, inventory, stock movements, approvals")
    pending_inventory = pending_approvals = 0
    if user.role == "MANAGER":
        from django.db.models import Q
        from production.models import Distribution
        from sales.models import OutletTransfer, PendingAction, StockReturn
        branch = user.branch
        pending_inventory = (
            Distribution.objects.filter(business=user.business, status="SENT", receiver_location__branch=branch).count()
            + OutletTransfer.objects.filter(business=user.business, status="SENT", to_location__branch=branch).count()
            + StockReturn.objects.filter(business=user.business, status__in=["SENT", "DISPUTED"], to_location__branch=branch).count()
        )
        pending_approvals = PendingAction.objects.filter(
            business=user.business, status="PENDING", requested_by__branch=branch).count()
    pending_branch = pending_inventory + pending_approvals

    # Production's own factory: returns sent straight to them (a manager-
    # approved RETURN_TO_PRODUCTION), sitting at "Sent" until Production
    # itself confirms receipt
    pending_incoming_returns = 0
    if user.role == "PRODUCTION":
        from sales.models import StockReturn
        pending_incoming_returns = StockReturn.objects.filter(
            business=user.business, status__in=["SENT", "DISPUTED"], to_location__branch=user.branch).count()

    # the total behind the Notifications nav badge — everything the full
    # feed would show as still "in flight" for this account
    if user.role == "OWNER":
        from production.models import Distribution
        from sales.models import OutletTransfer, PendingAction, StockRequest, StockReturn
        pending_notifications = (
            Distribution.objects.filter(business=user.business, status__in=["SENT", "DISPUTED"]).count()
            + OutletTransfer.objects.filter(business=user.business, status__in=["SENT", "DISPUTED"]).count()
            + StockReturn.objects.filter(business=user.business, status__in=["SENT", "DISPUTED"]).count()
            + StockRequest.objects.filter(business=user.business, status__in=["SUBMITTED", "PARTIAL"]).count()
            + PendingAction.objects.filter(business=user.business, status="PENDING").count()
        )
    elif user.role == "MANAGER":
        pending_notifications = pending_branch + pending_stock_requests
    elif acting_role == "SALES_REP":
        pending_notifications = pending_deliveries
    elif user.role == "CASHIER":
        from sales.models import InventoryLocation, OutletTransfer
        outlet = InventoryLocation.objects.filter(branch=user.branch, type="OUTLET").first()
        pending_notifications = OutletTransfer.objects.filter(status="SENT", to_location=outlet).count() if outlet else 0
    elif user.role == "PRODUCTION":
        pending_notifications = pending_stock_requests + pending_incoming_returns
    else:
        pending_notifications = 0

    return {"pending_stock_requests": pending_stock_requests, "pending_deliveries": pending_deliveries,
            "pending_inventory": pending_inventory, "pending_approvals": pending_approvals,
            "pending_branch": pending_branch, "pending_notifications": pending_notifications,
            "pending_incoming_returns": pending_incoming_returns}
