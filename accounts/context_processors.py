def active_view(request):
    user = getattr(request, "user", None)
    return {
        "active_view": getattr(request, "active_view", None),
        "switchable_views": user.switchable_views() if user and user.is_authenticated else [],
        "owner_section": getattr(request, "owner_section", None),
        "manager_section": getattr(request, "manager_section", None),
    }
