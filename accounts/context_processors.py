def active_view(request):
    return {
        "active_view": getattr(request, "active_view", None),
        "switchable_views": ["MANAGER", "CASHIER", "PRODUCTION", "SALES_REP"],
    }
