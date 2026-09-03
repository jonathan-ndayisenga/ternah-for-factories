"""Manager view switcher. active_view lives in the session; only managers
may set it to something other than their own role. Every write elsewhere
should record acted_as = request.active_view for audit.

owner_section / manager_section are the finer-grained pick within a role's
own home-tile screen (Owner: Reports/Finance/Manage; Manager, while in her
own MANAGER view: Branch/Manage/Finance) — same idea as active_view, one
level down, so the sidebar can show just that section's links instead of
everything a role can reach stacked in one long list."""

OWNER_SECTIONS = ["reports", "finance", "manage"]
MANAGER_SECTIONS = ["branch", "manage", "finance"]


class ActiveViewMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user and user.is_authenticated and not user.is_superuser:
            default = user.role
            view = request.session.get("active_view", default)
            if user.role != "MANAGER":
                view = default                      # only managers switch
            elif view != default and view not in user.switchable_views():
                # the module backing this view (e.g. PRODUCTION) may have been
                # revoked since they switched — don't honor a stale session
                view = default
            request.active_view = view

            if user.role == "OWNER":
                section = request.session.get("owner_section")
                request.owner_section = section if section in OWNER_SECTIONS else OWNER_SECTIONS[0]
            else:
                request.owner_section = None

            if user.role == "MANAGER":
                section = request.session.get("manager_section")
                request.manager_section = section if section in MANAGER_SECTIONS else MANAGER_SECTIONS[0]
            else:
                request.manager_section = None
        else:
            request.active_view = None
            request.owner_section = None
            request.manager_section = None
        return self.get_response(request)
