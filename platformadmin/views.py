"""Software-owner console: stats dashboard, wizard onboarding, and per-business
management (modules, subscription length, lock/unlock, audit history).
Guarded by is_superuser throughout."""
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import user_passes_test
from django.db import models
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import slugify

from core.models import Branch
from sales.models import InventoryLocation
from .models import AuditLog, Business, Module, ModuleSubscription, SubscriptionExtension, add_months

superuser_required = user_passes_test(lambda u: u.is_superuser)

FACTORY_MODULES = ["PRODUCTION", "SALES", "DEBTORS", "EXPENSES", "FINANCE", "REPORTS"]


def _ensure_module_catalog():
    """Get-or-create the fixed Factory Mode catalog so onboarding's checkboxes
    always have something to show, even on a brand new install."""
    for code in FACTORY_MODULES:
        Module.objects.get_or_create(code=code, defaults={"name": code.title()})


def _safe_next(request, fallback_url):
    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return next_url
    return fallback_url


@superuser_required
def dashboard(request):
    businesses = list(Business.objects.all().order_by("-created_at"))
    for b in businesses:
        b.module_count = b.subscriptions.filter(is_active=True).count()
    stats = {
        "total": len(businesses),
        "active": sum(1 for b in businesses if b.billing_state == "ACTIVE"),
        "warning": sum(1 for b in businesses if b.billing_state == "WARNING"),
        "locked": sum(1 for b in businesses if b.billing_state in ("SOFT_LOCKED", "EXPIRED")),
        "mrr": ModuleSubscription.objects.filter(is_active=True, business__is_active=True)
                    .aggregate(s=models.Sum("price_per_cycle"))["s"] or 0,
    }
    return render(request, "platformadmin/dashboard.html", {"businesses": businesses, "stats": stats})


@superuser_required
def onboard(request):
    """One popup, four steps: business & contact -> admin account -> modules
    -> subscription length. Only the modules ticked here get switched on."""
    _ensure_module_catalog()
    modules = Module.objects.all().order_by("code")
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        try:
            months = max(1, int(request.POST.get("months", 1)))
        except (TypeError, ValueError):
            months = 1
        if not name:
            return render(request, "platformadmin/onboard.html", {"modules": modules, "error": "Factory name is required."})

        now = timezone.now()
        biz = Business.objects.create(
            name=name, slug=slugify(name),
            contact_email=request.POST.get("contact_email", ""),
            contact_phone=request.POST.get("contact_phone", ""),
            address=request.POST.get("address", ""),
            subscription_expires_at=add_months(now, months),
        )
        # factory branch is created automatically — production lives here
        factory = Branch.objects.create(business=biz, name="Factory", kind="FACTORY")
        InventoryLocation.objects.create(business=biz, branch=factory, type="PRODUCTION_STORE")

        User = get_user_model()
        admin = User.objects.create_user(
            username=request.POST["admin_username"], password=request.POST["admin_password"],
            business=biz, branch=factory, role="MANAGER",
        )

        selected = set(request.POST.getlist("modules"))
        for module in modules:
            if str(module.pk) in selected:
                ModuleSubscription.objects.create(
                    business=biz, module=module, price_per_cycle=module.default_price_per_cycle)

        SubscriptionExtension.objects.create(
            business=biz, months=months, filed_by=request.user,
            reason="Initial subscription at onboarding")
        AuditLog.write("TENANT_CREATED", actor=request.user, business=biz,
                       description=f"Onboarded {biz.name}; admin={admin.username}; {months} month(s)")
        return redirect("platformadmin:business_detail", pk=biz.pk)
    return render(request, "platformadmin/onboard.html", {"modules": modules})


@superuser_required
def business_detail(request, pk):
    biz = get_object_or_404(Business, pk=pk)
    _ensure_module_catalog()
    all_modules = list(Module.objects.all().order_by("code"))
    subscribed_ids = set(biz.subscriptions.filter(is_active=True).values_list("module_id", flat=True))
    return render(request, "platformadmin/business_detail.html", {
        "business": biz, "modules": all_modules, "subscribed_ids": subscribed_ids,
        "extensions": biz.extensions.order_by("-created_at")[:20],
        "audit": AuditLog.objects.filter(business=biz).order_by("-created_at")[:30],
    })


@superuser_required
def toggle_module(request, pk, module_pk):
    biz = get_object_or_404(Business, pk=pk)
    module = get_object_or_404(Module, pk=module_pk)
    if request.method == "POST":
        sub = ModuleSubscription.objects.filter(business=biz, module=module).first()
        if sub and sub.is_active:
            sub.is_active = False
            sub.save(update_fields=["is_active"])
            AuditLog.write("MODULE_REMOVED", actor=request.user, business=biz, description=module.name)
        elif sub:
            sub.is_active = True
            sub.save(update_fields=["is_active"])
            AuditLog.write("MODULE_SUBSCRIBED", actor=request.user, business=biz, description=module.name)
        else:
            ModuleSubscription.objects.create(business=biz, module=module,
                                              price_per_cycle=module.default_price_per_cycle)
            AuditLog.write("MODULE_SUBSCRIBED", actor=request.user, business=biz, description=module.name)
    return redirect("platformadmin:business_detail", pk=biz.pk)


@superuser_required
def extend_subscription(request, pk):
    """Months, not tokens — do the calendar math properly (add_months), not a
    30-days-per-unit approximation. Also doubles as reactivation (SaaS ref §06)."""
    biz = get_object_or_404(Business, pk=pk)
    if request.method == "POST":
        try:
            months = int(request.POST.get("months", 0))
        except (TypeError, ValueError):
            months = 0
        if months:
            base = biz.subscription_expires_at or timezone.now()
            if base < timezone.now():
                base = timezone.now()   # lapsed — extend from today, not from a stale past date
            biz.subscription_expires_at = add_months(base, months)
            was_locked = not biz.is_active
            biz.is_active = True
            biz.save()
            SubscriptionExtension.objects.create(
                business=biz, months=months, filed_by=request.user,
                reason=request.POST.get("reason", "").strip() or "Subscription extension",
                reference=request.POST.get("reference", "").strip(),
            )
            AuditLog.write("SUBSCRIPTION_EXTENDED", actor=request.user, business=biz,
                           description=f"+{months} month(s); expiry -> {biz.subscription_expires_at:%Y-%m-%d}")
            if was_locked:
                AuditLog.write("ACCOUNT_ACTIVATED", actor=request.user, business=biz,
                               description="Reactivated via subscription extension")
    return redirect(_safe_next(request, reverse("platformadmin:business_detail", args=[biz.pk])))


@superuser_required
def toggle_active(request, pk):
    biz = get_object_or_404(Business, pk=pk)
    if request.method == "POST":
        biz.is_active = not biz.is_active
        biz.save(update_fields=["is_active"])
        AuditLog.write("ACCOUNT_ACTIVATED" if biz.is_active else "ACCOUNT_LOCKED",
                       actor=request.user, business=biz)
    return redirect(_safe_next(request, reverse("platformadmin:business_detail", args=[biz.pk])))
