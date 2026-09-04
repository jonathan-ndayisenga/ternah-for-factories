from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from core.models import Branch
from platformadmin.models import Module
from sales.models import InventoryLocation
from .middleware import MANAGER_SECTIONS, OWNER_SECTIONS
from .models import PRICE_TIERS, Note, User


@login_required
def switch_view(request, view):
    # server-side, not just a hidden button — hitting this URL directly for a
    # view outside what's actually granted must not work either.
    if request.user.role == "MANAGER" and view in request.user.switchable_views():
        request.session["active_view"] = view
    return redirect("home")


# Home-tile screen: Owner and Manager each have more than one place to go,
# so login lands them on a picker instead of guessing which one they meant.
# Everyone else has exactly one destination and skips this entirely — see
# config/urls.py's home() view.
OWNER_SECTION_LANDING = {"reports": "reports:owner_dashboard", "finance": "finance:financial_reports",
                         "manage": "core:branches"}
MANAGER_SECTION_LANDING = {"branch": "manager:dashboard", "manage": "user_list", "finance": "manager:debtors"}


def owner_tiles():
    icons = {
        "reports": '<path d="M4 19V10M12 19V5M20 19v-6"/><path d="M3 19h18"/>',
        "finance": '<path d="M3 3v18h18"/><path d="M18 17V9M13 17V5M8 17v-4"/>',
        "manage": '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    }
    return [
        {"key": "reports", "label": "Reports", "icon": icons["reports"],
         "desc": "Dashboard, production, stock movements, debtors, rep performance",
         "href": reverse("select_section", args=["reports"])},
        {"key": "finance", "label": "Finance", "icon": icons["finance"],
         "desc": "Trial balance, profit & loss, balance sheet, revenue by product",
         "href": reverse("select_section", args=["finance"])},
        {"key": "manage", "label": "Manage", "icon": icons["manage"],
         "desc": "Branches, users, product pricing, business settings",
         "href": reverse("select_section", args=["manage"])},
    ]


def single_role_tile(role):
    """Cashier, Sales Rep, and Production each have exactly one place to go
    — still shown as a real Home screen (one tile) rather than skipped
    straight past, so Home behaves the same way for everyone and is always
    a real landing page to come back to, not a special case for Owner/
    Manager only."""
    icons = {
        "CASHIER": '<circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"/>',
        "PRODUCTION": '<path d="M10 2v6.5L4.5 19a2 2 0 0 0 1.7 3h11.6a2 2 0 0 0 1.7-3L14 8.5V2"/><path d="M8.5 2h7M7 15h10"/>',
        "SALES_REP": '<circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"/>',
    }
    labels = {"CASHIER": "Cashier", "SALES_REP": "Sales Rep", "PRODUCTION": "Production"}
    descs = {"CASHIER": "Ring up sales at your outlet", "SALES_REP": "Sell out of your own stock",
             "PRODUCTION": "Formulas, batches, distribution, stock requests"}
    hrefs = {"CASHIER": reverse("sales:pos"), "SALES_REP": reverse("sales:pos"),
             "PRODUCTION": reverse("production:dashboard")}
    return [{"key": role.lower(), "label": labels[role], "icon": icons[role], "desc": descs[role], "href": hrefs[role]}]


def manager_tiles(user):
    icons = {
        "branch": '<path d="M4 19V10M12 19V5M20 19v-6"/><path d="M3 19h18"/>',
        "manage": '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
        "finance": '<path d="M3 3v18h18"/><path d="M18 17V9M13 17V5M8 17v-4"/>',
        "CASHIER": '<circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"/>',
        "PRODUCTION": '<path d="M10 2v6.5L4.5 19a2 2 0 0 0 1.7 3h11.6a2 2 0 0 0 1.7-3L14 8.5V2"/><path d="M8.5 2h7M7 15h10"/>',
        "SALES_REP": '<circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"/>',
    }
    tiles = [
        {"key": "branch", "label": "Branch", "icon": icons["branch"],
         "desc": "Dashboard, inventory, stock movements, approvals",
         "href": reverse("select_section", args=["branch"])},
        {"key": "manage", "label": "Manage", "icon": icons["manage"],
         "desc": "Staff at your branch", "href": reverse("select_section", args=["manage"])},
        {"key": "finance", "label": "Finance", "icon": icons["finance"],
         "desc": "Debtors, cashbook, ledger, expenses, payment accounts",
         "href": reverse("select_section", args=["finance"])},
    ]
    labels = {"CASHIER": "Cashier", "PRODUCTION": "Production", "SALES_REP": "Sales Rep"}
    descs = {"CASHIER": "Ring up sales at your outlet", "PRODUCTION": "Formulas, batches, distribution",
             "SALES_REP": "Sell out of your own stock"}
    for v in user.switchable_views():
        if v == "MANAGER":
            continue
        tiles.append({"key": v.lower(), "label": labels.get(v, v.title()), "icon": icons.get(v, icons["branch"]),
                      "desc": descs.get(v, ""), "href": f"/accounts/switch/{v}/"})
    return tiles


@login_required
def select_section(request, key):
    if request.user.role == "OWNER" and key in OWNER_SECTIONS:
        request.session["owner_section"] = key
        return redirect(OWNER_SECTION_LANDING[key])
    if request.user.role == "MANAGER" and key in MANAGER_SECTIONS:
        request.session["manager_section"] = key
        request.session["active_view"] = "MANAGER"   # a section pick always means "my own view", not a switched one
        return redirect(MANAGER_SECTION_LANDING[key])
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

        new_password = request.POST.get("new_password", "").strip()
        if new_password:
            target.set_password(new_password)
            target.save(update_fields=["password"])
            messages.success(request, f"Password reset for {target.username}. Write it down now — "
                                      f"it can't be looked up again once you leave this page. "
                                      f"Your Notes page is a good place to keep it.")
        return redirect("user_list")

    return render(request, "accounts/user_edit.html", {
        "target": target, "available_modules": available_modules, "tier_choices": PRICE_TIERS,
    })


@login_required
@can_manage_users
def notes_list(request):
    """A private scratchpad — never shared, not even with another owner or
    manager at the same business. The obvious use: the moment you reset
    someone's password (see user_edit above), write it here — Django never
    stores it anywhere you could look back up."""
    if request.method == "POST":
        body = request.POST.get("body", "").strip()
        if body:
            Note.objects.create(user=request.user, title=request.POST.get("title", "").strip(), body=body)
        return redirect("notes")
    return render(request, "accounts/notes.html", {"notes": request.user.notes.all()})


@login_required
@can_manage_users
def note_delete(request, pk):
    note = get_object_or_404(Note, pk=pk, user=request.user)
    if request.method == "POST":
        note.delete()
    return redirect("notes")
