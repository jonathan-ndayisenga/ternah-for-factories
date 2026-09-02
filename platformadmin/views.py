"""Software-owner console: onboarding (5 steps in one form), tenant list,
token top-ups, activate/deactivate. Guarded by is_superuser."""
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from datetime import timedelta

from core.models import Branch
from sales.models import InventoryLocation
from .models import AuditLog, Business, Module, ModuleSubscription, TokenTransaction

superuser_required = user_passes_test(lambda u: u.is_superuser)

FACTORY_MODULES = ["PRODUCTION", "SALES", "DEBTORS", "EXPENSES", "FINANCE", "REPORTS"]


@superuser_required
def dashboard(request):
    businesses = Business.objects.all().order_by("-created_at")
    return render(request, "platformadmin/dashboard.html", {"businesses": businesses})


@superuser_required
def onboard(request):
    """SaaS ref §03 — the five steps, one screen:
    1 tenant record  2 business admin  3 modules  4 tokens  5 handoff."""
    if request.method == "POST":
        name = request.POST["name"].strip()
        biz = Business.objects.create(
            name=name, slug=slugify(name),
            contact_email=request.POST["contact_email"],
            contact_phone=request.POST.get("contact_phone", ""),
            address=request.POST.get("address", ""),
            subscription_expires_at=timezone.now() + timedelta(days=int(request.POST.get("period_days", 30))),
            token_balance=int(request.POST.get("token_balance", 0)),
        )
        # factory branch is created automatically — production lives here
        factory = Branch.objects.create(business=biz, name="Factory", kind="FACTORY")
        InventoryLocation.objects.create(business=biz, branch=factory, type="PRODUCTION_STORE")
        # step 2 — business admin (the manager the factory hands off to)
        User = get_user_model()
        admin = User.objects.create_user(
            username=request.POST["admin_username"],
            password=request.POST["admin_password"],
            business=biz, branch=factory, role="MANAGER",
        )
        # step 3 — Factory Mode fills in every module
        for code in FACTORY_MODULES:
            module, _ = Module.objects.get_or_create(code=code, defaults={"name": code.title()})
            ModuleSubscription.objects.create(business=biz, module=module)
        # step 4 — tokens
        if biz.token_balance:
            TokenTransaction.objects.create(business=biz, amount=biz.token_balance,
                                            reason="Initial top-up at onboarding", filed_by=request.user)
        AuditLog.write("TENANT_CREATED", actor=request.user, business=biz,
                       description=f"Onboarded {biz.name} in Factory Mode; admin={admin.username}")
        return redirect("platformadmin:dashboard")
    return render(request, "platformadmin/onboard.html")


@superuser_required
def top_up(request, pk):
    biz = get_object_or_404(Business, pk=pk)
    if request.method == "POST":
        amount = int(request.POST["amount"])
        biz.token_balance += amount
        if not biz.is_active:                       # reactivation — SaaS ref §06
            biz.is_active = True
        biz.subscription_expires_at = max(
            biz.subscription_expires_at or timezone.now(), timezone.now()
        ) + timedelta(days=30 * amount)
        biz.save()
        TokenTransaction.objects.create(business=biz, amount=amount,
                                        reason="Top-up by client", filed_by=request.user,
                                        reference=request.POST.get("reference", ""))
        AuditLog.write("TOKEN_TOP_UP", actor=request.user, business=biz,
                       description=f"+{amount} tokens; expiry -> {biz.subscription_expires_at:%Y-%m-%d}")
    return redirect("platformadmin:dashboard")


@superuser_required
def toggle_active(request, pk):
    biz = get_object_or_404(Business, pk=pk)
    biz.is_active = not biz.is_active
    biz.save()
    AuditLog.write("ACCOUNT_ACTIVATED" if biz.is_active else "ACCOUNT_LOCKED",
                   actor=request.user, business=biz)
    return redirect("platformadmin:dashboard")
