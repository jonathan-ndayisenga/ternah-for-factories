"""
Software-owner side (SaaS Platform Administration Reference):
one super user above all tenants; tenant access is a function of billing
state — active, warned, soft-locked, reactivated.
"""
import calendar
from django.conf import settings
from django.db import models
from django.utils import timezone


def add_months(dt, months):
    """Real calendar-month arithmetic, not a 30-day approximation — 31 Jan + 1
    month lands on 28/29 Feb, not "31 days later". Works on date or datetime."""
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


class Business(models.Model):
    """The tenant. Every other model reaches this through its branch/user FK."""
    name = models.CharField(max_length=150)                     # factory name — brands navbar + receipts
    slug = models.SlugField(unique=True)
    business_type = models.CharField(max_length=20, choices=[("FACTORY", "Factory Mode")], default="FACTORY")
    contact_email = models.EmailField()
    contact_phone = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)
    tagline = models.CharField(max_length=150, blank=True)      # optional — shows on every printed report
    is_active = models.BooleanField(default=True)               # master switch — False = soft-locked
    trial_expires_at = models.DateTimeField(null=True, blank=True)
    subscription_expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "businesses"

    def __str__(self):
        return self.name

    @property
    def billing_state(self):
        if not self.is_active:
            return "SOFT_LOCKED"
        if self.subscription_expires_at:
            days = (self.subscription_expires_at - timezone.now()).days
            if days < 0:
                return "EXPIRED"       # grace window before the cron sets is_active=False
            if days <= 7:
                return "WARNING"
        return "ACTIVE"


class Module(models.Model):
    """Factory Mode module catalog: PRODUCTION, SALES, FINANCE, REPORTS, DEBTORS, EXPENSES."""
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=80)
    default_price_per_cycle = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def __str__(self):
        return self.name


class ModuleSubscription(models.Model):
    """Drives every module check — no active subscription, module invisible."""
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="subscriptions")
    module = models.ForeignKey(Module, on_delete=models.CASCADE)
    is_active = models.BooleanField(default=True)
    subscribed_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    price_per_cycle = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        unique_together = [("business", "module")]


class SubscriptionExtension(models.Model):
    """Every time the operator adds months to a business's subscription — the
    audit trail behind subscription_expires_at. Positive months only; a
    correction/shortening is a separate negative-months entry, never an edit."""
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="extensions")
    months = models.IntegerField()
    reason = models.CharField(max_length=120)
    filed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    reference = models.CharField(max_length=120, blank=True)    # bank slip / MoMo code
    created_at = models.DateTimeField(auto_now_add=True)


class AuditLog(models.Model):
    """Append-only. Never updated, never deleted."""
    business = models.ForeignKey(Business, null=True, blank=True, on_delete=models.SET_NULL)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    actor_role = models.CharField(max_length=40, blank=True)    # denormalized on purpose
    action = models.CharField(max_length=60)                    # TENANT_CREATED, TOKEN_TOP_UP, ACCOUNT_LOCKED...
    description = models.TextField(blank=True)
    object_type = models.CharField(max_length=60, blank=True)
    object_id = models.CharField(max_length=40, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def write(cls, action, actor=None, business=None, description="", **meta):
        return cls.objects.create(
            action=action, actor=actor, business=business, description=description,
            actor_role=getattr(actor, "role", "") if actor else "", metadata=meta,
        )
