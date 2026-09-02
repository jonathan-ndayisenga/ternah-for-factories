"""Manager view switcher. active_view lives in the session; only managers
may set it to something other than their own role. Every write elsewhere
should record acted_as = request.active_view for audit."""
from .models import VIEWS


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
            request.active_view = view
        else:
            request.active_view = None
        return self.get_response(request)
