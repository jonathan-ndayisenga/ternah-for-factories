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


VIEWS = ["MANAGER", "CASHIER", "PRODUCTION", "SALES_REP"]
PRICE_TIERS = [("RETAIL", "Retail"), ("WHOLESALE", "Wholesale"),
               ("DISTRIBUTION", "Distribution"), ("CUSTOM", "Custom")]
