from django.db import models


class TimeStamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Branch(TimeStamped):
    """
    The spine of Ternah for Factories.
    Every user, inventory, sale, expense and debtor hangs off a branch,
    so the owner's per-branch reports roll up cleanly.
    kind: FACTORY = the production site itself; OUTLET = a shop.
    Sales reps are users attached to a branch — their personal inventory
    still rolls up under that branch in owner reports.
    """
    KIND = [("FACTORY", "Factory / Production site"), ("OUTLET", "Outlet / Shop")]
    business = models.ForeignKey("platformadmin.Business", on_delete=models.CASCADE, related_name="branches")
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=10, choices=KIND, default="OUTLET")
    address = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "branches"
        unique_together = [("business", "name")]

    def __str__(self):
        return f"{self.name} ({self.get_kind_display()})"
