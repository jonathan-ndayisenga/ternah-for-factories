from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import get_object_or_404, redirect, render

from core.models import Branch
from platformadmin.models import Module
from sales.models import InventoryLocation
from .models import PRICE_TIERS, User


@login_required
def switch_view(request, view):
    # server-side, not just a hidden button — hitting this URL directly for a
    # view outside what's actually granted must not work either.
    if request.user.role == "MANAGER" and view in request.user.switchable_views():
        request.session["active_view"] = view
    return redirect("home")


# Who can create whom — SaaS ref: owner manages the business, a manager only
# manages the staff below them at their own branch.
OWNER_CREATABLE_ROLES = ["MANAGER", "CASHIER", "SALES_REP", "PRODUCTION"]
MANAGER_CREATABLE_ROLES = ["CASHIER", "SALES_REP", "PRODUCTION"]
ROLE_LABELS = dict(User.ROLES)
TIER_ROLES = ("CASHIER", "SALES_REP")   # only sellers get a price-tier permission set

can_manage_users = user_passes_test(lambda u: u.is_authenticated and u.role in ("OWNER", "MANAGER"))


def _modules_for_role(biz, role):
    """The Production module only makes sense for Production staff, or a
    manager the owner has trusted with it too (it's what makes 'Production'
    show up as a switchable view in her own nav) — a cashier or rep never
    needs it offered, regardless of what else the business subscribes to."""
    modules = Module.objects.filter(modulesubscription__business=biz, modulesubscription__is_active=True).distinct()
    if role not in ("PRODUCTION", "MANAGER"):
        modules = modules.exclude(code="PRODUCTION")
    return modules


@can_manage_users
def user_list(request):
    """Only ever list users this viewer can actually edit — a manager isn't
    allowed to manage another manager (or themselves) here, so those rows
    used to appear with an Edit link that 404'd. Filtering by the same
    allowed-roles set the edit view checks means every row's link works."""
    biz = request.user.business
    is_owner = request.user.role == "OWNER"
    allowed_roles = OWNER_CREATABLE_ROLES if is_owner else MANAGER_CREATABLE_ROLES
    users = User.objects.filter(business=biz, role__in=allowed_roles).select_related("branch").prefetch_related("modules")
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
            user.modules.set(_modules_for_role(biz, role).filter(id__in=module_ids))
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
    available_modules = _modules_for_role(biz, target.role)

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
