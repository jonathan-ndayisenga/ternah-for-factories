"""Soft-lock middleware — SaaS reference §07.
Locked tenants can still log in and see the shell; all operational
endpoints render the locked template. Super user bypasses everything."""
from django.shortcuts import render

WHITELIST_PREFIXES = ("/accounts/login", "/accounts/logout", "/accounts/profile",
                      "/platform", "/admin", "/static", "/billing/top-up")


class SoftLockMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user if hasattr(request, "user") else None
        if user and user.is_authenticated and not user.is_superuser:
            biz = getattr(user, "business", None)
            if biz and not biz.is_active and not request.path.startswith(WHITELIST_PREFIXES):
                if request.headers.get("HX-Request"):
                    return render(request, "partials/locked_banner.html", status=423)
                return render(request, "locked.html", {"business": biz}, status=423)
        return self.get_response(request)
