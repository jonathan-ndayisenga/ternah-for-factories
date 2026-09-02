from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import get_object_or_404, redirect, render

from core.models import Branch
from platformadmin.models import Module
from sales.models import InventoryLocation
from .models import PRICE_TIERS, VIEWS, User


@login_required
def switch_view(request, view):
    if request.user.role == "MANAGER" and view in VIEWS:
        request.session["active_view"] = view
    return redirect("home")


# Who can create whom — SaaS ref: owner manages the business, a manager only
# manages the staff below them at their own branch.
OWNER_CREATABLE_ROLES = ["MANAGER", "CASHIER", "SALES_REP", "PRODUCTION"]
MANAGER_CREATABLE_ROLES = ["CASHIER", "SALES_REP", "PRODUCTION"]
ROLE_LABELS = dict(User.ROLES)
TIER_ROLES = ("CASHIER", "SALES_REP")   # only sellers get a price-tier permission set

can_manage_users = user_passes_test(lambda u: u.is_authenticated and u.role in ("OWNER", "MANAGER"))


@can_manage_users
def user_list(request):
    biz = request.user.business
    users = User.objects.filter(business=biz).exclude(role="OWNER").select_related("branch").prefetch_related("modules")
    if request.user.role == "MANAGER":
        users = users.filter(branch=request.user.branch)
    return render(request, "accounts/users.html", {"users": users.order_by("branch__name", "username")})


@can_manage_users
def user_create(request):
    biz = request.user.business
    is_owner = request.user.role == "OWNER"
    allowed_roles = OWNER_CREATABLE_ROLES if is_owner else MANAGER_CREATABLE_ROLES
    role_choices = [(code, ROLE_LABELS[code]) for code in allowed_roles]
    branches = biz.branches.all() if is_owner else None
    available_modules = Module.objects.filter(modulesubscription__business=biz, modulesubscription__is_active=True).distinct()

    if request.method == "POST":
        role = request.POST.get("role")
        if role not in allowed_roles:
            return redirect("user_list")   # tampered role outside what this creator may assign
        branch = get_object_or_404(Branch, pk=request.POST["branch"], business=biz) if is_owner \
            else request.user.branch
        user = User.objects.create_user(
            username=request.POST["username"].strip(), password=request.POST["password"],
            business=biz, branch=branch, role=role,
            first_name=request.POST.get("first_name", "").strip(),
            last_name=request.POST.get("last_name", "").strip(),
            phone=request.POST.get("phone", "").strip(),
            can_swap="can_swap" in request.POST, can_refund="can_refund" in request.POST,
            allowed_tiers=request.POST.getlist("allowed_tiers") if role in TIER_ROLES else [],
        )
        module_ids = request.POST.getlist("modules")
        if module_ids:
            user.modules.set(available_modules.filter(id__in=module_ids))
        if role == "SALES_REP":
            # a rep sells out of their own personal inventory, same shape as an outlet's
            InventoryLocation.objects.create(business=biz, branch=branch, type="REP", rep=user)
        return redirect("user_list")

    return render(request, "accounts/user_create.html", {
        "role_choices": role_choices, "branches": branches, "available_modules": available_modules,
        "tier_choices": PRICE_TIERS,
    })


@can_manage_users
def user_edit(request, pk):
    biz = request.user.business
    is_owner = request.user.role == "OWNER"
    allowed_roles = OWNER_CREATABLE_ROLES if is_owner else MANAGER_CREATABLE_ROLES
    target = get_object_or_404(User, pk=pk, business=biz, role__in=allowed_roles)
    if request.user.role == "MANAGER" and target.branch_id != request.user.branch_id:
        return redirect("user_list")   # a manager only manages their own branch's staff
    available_modules = Module.objects.filter(modulesubscription__business=biz, modulesubscription__is_active=True).distinct()

    if request.method == "POST":
        target.can_swap = "can_swap" in request.POST
        target.can_refund = "can_refund" in request.POST
        target.is_active = "is_active" in request.POST
        if target.role in TIER_ROLES:
            target.allowed_tiers = request.POST.getlist("allowed_tiers")
        target.save()
        target.modules.set(available_modules.filter(id__in=request.POST.getlist("modules")))
        return redirect("user_list")

    return render(request, "accounts/user_edit.html", {
        "target": target, "available_modules": available_modules, "tier_choices": PRICE_TIERS,
    })
