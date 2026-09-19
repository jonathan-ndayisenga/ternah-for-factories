from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """
    Every user belongs to a business AND a branch (super user: both NULL).
    Reps are branch users too — their sales, stock, debtors and expenses
    all roll up under their branch in the owner's reports.
    """
    ROLES = [
        ("OWNER", "Owner"),                # reports only
        ("MANAGER", "Manager"),            # outlet manager; can switch views
        ("CASHIER", "Cashier"),
        ("SALES_REP", "Sales Person / Rep"),
        ("PRODUCTION", "Production"),
    ]
    business = models.ForeignKey("platformadmin.Business", null=True, blank=True,
                                 on_delete=models.CASCADE, related_name="users")
    branch = models.ForeignKey("core.Branch", null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="users")
    role = models.CharField(max_length=15, choices=ROLES, default="CASHIER")
    can_swap = models.BooleanField(default=False)    # False -> swap goes through PendingAction
    can_refund = models.BooleanField(default=False)
    phone = models.CharField(max_length=30, blank=True)
    modules = models.ManyToManyField("platformadmin.Module", blank=True, related_name="users_with_access",
                                     help_text="Which of the business's subscribed modules this user can open.")
    allowed_tiers = models.JSONField(default=list, blank=True,
                                     help_text="Which product price tiers (RETAIL/WHOLESALE/DISTRIBUTION/CUSTOM) "
                                                "this seller may use at the POS.")

    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"

    def switchable_views(self):
        """Which views a manager may switch into. Manager/Cashier are the
        base capability, always available; Production/Sales Rep are extra
        privileges gated on the matching module actually being assigned —
        not automatic just because the manager role can switch at all."""
        if self.role != "MANAGER":
            return []
        views = ["MANAGER", "CASHIER"]
        codes = set(self.modules.values_list("code", flat=True))
        if "PRODUCTION" in codes:
            views.append("PRODUCTION")
        if "SALES" in codes:
            views.append("SALES_REP")
        return views


VIEWS = ["MANAGER", "CASHIER", "PRODUCTION", "SALES_REP"]
PRICE_TIERS = [("RETAIL", "Retail"), ("WHOLESALE", "Wholesale"),
               ("DISTRIBUTION", "Distribution"), ("CUSTOM", "Custom")]


class Note(models.Model):
    """A private scratchpad, per user — not shared with anyone else, even
    another owner/manager at the same business. Built for jotting down a
    password the moment you reset one for someone else, since Django never
    stores it in a form you could look back up."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notes")
    title = models.CharField(max_length=120, blank=True)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]


class NotificationDismissal(models.Model):
    """A user has followed a notification through to where it's handled —
    it drops off their own feed from then on. Deliberately NOT wired into
    the badge counts (Inventory/Approvals/tile/nav) — those stay purely
    driven by the underlying record's real status, so dismissing a
    notification you've merely looked at can never make a still-pending
    item silently stop asking for attention. Naming it "dismissal" rather
    than "read" for that reason: it means "stop showing me this", not
    "this is resolved"."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notification_dismissals")
    kind = models.CharField(max_length=20)     # "distribution", "outlet_transfer", "stock_return", "stock_request", "approval"
    object_id = models.PositiveIntegerField()
    dismissed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("user", "kind", "object_id")]
