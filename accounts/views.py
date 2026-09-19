from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

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


_NOTIF_STATUS_KIND = {
    "SENT": "warning", "SUBMITTED": "warning", "PARTIAL": "warning", "PENDING": "warning",
    "PLANNED": "warning", "DISPENSED": "warning",
    "DISPUTED": "locked", "REJECTED": "locked",
    "RECEIVED": "active", "FULFILLED": "active", "APPROVED": "active", "COMPLETED": "active", "POSTED": "active",
}
_NOTIF_KIND_LABELS = [
    ("distribution", "Distribution"), ("outlet_transfer", "Outlet Transfer"), ("stock_return", "Stock Return"),
    ("stock_request", "Stock Request"), ("approval", "Approval"), ("batch", "Batch Produced"),
    ("transaction", "Transaction"),
]


@login_required
def notifications(request):
    """A single feed of every stock-moving event (and the approval requests
    that ride along with them) touching this user's own account — the thing
    the badges on Home tiles and specific nav links (Inventory, Approvals,
    Items Received...) are hinting at. Scoped by branch/personal location so
    everyone sees only what's relevant to them; Owner sees the whole
    business but gets no action links here — approving/confirming stays a
    Manager job, this is Owner's window into it, not a second inbox.

    Following a "View in ..." link dismisses that row from this feed (see
    notification_go) — a personal "seen it" marker, not a status change, so
    the badges elsewhere (which read the real record) never go stale just
    because someone clicked through without finishing the job."""
    from .models import NotificationDismissal
    from production.models import Distribution, Product
    from sales.models import OutletTransfer, PendingAction, StockRequest, StockReturn

    user = request.user
    biz = user.business
    is_owner = user.role == "OWNER"
    has_production_access = user.role in ("OWNER", "PRODUCTION") or (
        user.role == "MANAGER" and user.modules.filter(code="PRODUCTION").exists())
    dismissed = set(NotificationDismissal.objects.filter(user=user).values_list("kind", "object_id"))
    events = []

    def add(when, kind_code, kind_label, pk, reference, from_label, to_label, items, status, status_display, dest_label, dest_url):
        if (kind_code, pk) in dismissed:
            return
        go_url = f"{reverse('notification_go', args=[kind_code, pk])}?next={dest_url}" if dest_url else None
        events.append({
            "date": when, "kind_code": kind_code, "kind": kind_label, "reference": reference,
            "from_label": from_label, "to_label": to_label,
            "items": items, "status": status, "status_display": status_display,
            "status_kind": _NOTIF_STATUS_KIND.get(status, "warning"),
            "dest_label": dest_label, "dest_url": go_url,
        })

    def items_line(lines, product_attr="product"):
        return ", ".join(f"{getattr(l, product_attr).name} ×{l.quantity}" for l in lines)

    # ---- Distribution: Production -> a branch outlet or a rep ----
    dist_qs = Distribution.objects.filter(business=biz).select_related(
        "sender_branch", "receiver_location__branch", "receiver_location__rep")
    if not is_owner:
        if user.role == "PRODUCTION":
            dist_qs = dist_qs.filter(sender_branch=user.branch)
        elif user.role == "SALES_REP":
            loc = getattr(user, "inventory", None)
            dist_qs = dist_qs.filter(receiver_location=loc) if loc else dist_qs.none()
        elif user.role == "MANAGER":
            dist_qs = dist_qs.filter(receiver_location__branch=user.branch)
        else:
            dist_qs = dist_qs.none()
    for d in dist_qs.prefetch_related("lines__product").order_by("-date", "-id")[:60]:
        to = d.receiver_location.rep.username if d.receiver_location.rep_id else d.receiver_location.get_type_display()
        dest = None
        if user.role == "PRODUCTION" or (user.role == "MANAGER" and request.active_view == "PRODUCTION"):
            dest = ("Distributions", reverse("production:distributions"))
        elif user.role == "SALES_REP":
            dest = ("Items Received", reverse("sales:received_items"))
        elif user.role == "MANAGER":
            dest = ("Inventory", reverse("manager:inventory"))
        add(d.date, "distribution", "Distribution", d.pk, d.delivery_note_number,
            d.sender_branch.name if d.sender_branch else "—", to,
            items_line(d.lines.all()), d.status, d.get_status_display(),
            dest[0] if dest else None, dest[1] if dest else None)

    # ---- Outlet Transfer: one outlet selling to another ----
    ot_qs = OutletTransfer.objects.filter(business=biz).select_related(
        "from_location__branch", "to_location__branch")
    if not is_owner:
        if user.role == "CASHIER":
            loc = InventoryLocation.objects.filter(branch=user.branch, type="OUTLET").first()
            ot_qs = ot_qs.filter(Q(from_location=loc) | Q(to_location=loc)) if loc else ot_qs.none()
        elif user.role == "MANAGER":
            ot_qs = ot_qs.filter(Q(from_location__branch=user.branch) | Q(to_location__branch=user.branch))
        else:
            ot_qs = ot_qs.none()
    for t in ot_qs.prefetch_related("lines__product").order_by("-date", "-id")[:60]:
        dest = ("Outlet Transfers", reverse("sales:outlet_transfers")) if user.role == "CASHIER" else (
            ("Inventory", reverse("manager:inventory")) if user.role == "MANAGER" else (None, None))
        add(t.date, "outlet_transfer", "Outlet Transfer", t.pk, t.reference_number,
            t.from_location.branch.name, t.to_location.branch.name,
            items_line(t.lines.all()), t.status, t.get_status_display(), dest[0], dest[1])

    # ---- Stock Return: a rep sending stock back to their branch's outlet ----
    ret_qs = StockReturn.objects.filter(business=biz).select_related(
        "from_location__branch", "from_location__rep", "to_location__branch")
    if not is_owner:
        if user.role == "SALES_REP":
            loc = getattr(user, "inventory", None)
            ret_qs = ret_qs.filter(from_location=loc) if loc else ret_qs.none()
        elif user.role == "MANAGER":
            ret_qs = ret_qs.filter(to_location__branch=user.branch)
        else:
            ret_qs = ret_qs.none()
    for r in ret_qs.prefetch_related("lines__product").order_by("-date", "-id")[:60]:
        dest = ("Return Stock", reverse("sales:stock_return_create")) if user.role == "SALES_REP" else (
            ("Inventory", reverse("manager:inventory")) if user.role == "MANAGER" else (None, None))
        add(r.date, "stock_return", "Stock Return", r.pk, r.reference_number,
            r.from_location.rep.username if r.from_location.rep_id else r.from_location.branch.name,
            r.to_location.branch.name, items_line(r.lines.all()), r.status, r.get_status_display(), dest[0], dest[1])

    # ---- Stock Request: a rep asking Production for more stock ----
    if is_owner or has_production_access or user.role == "SALES_REP":
        sr_qs = StockRequest.objects.filter(business=biz).select_related("requester_location__branch", "requester_location__rep")
        if user.role == "SALES_REP":
            loc = getattr(user, "inventory", None)
            sr_qs = sr_qs.filter(requester_location=loc) if loc else sr_qs.none()
        for r in sr_qs.order_by("-created_at")[:60]:
            names = {p.id: p.name for p in Product.objects.filter(pk__in=[l["product_id"] for l in r.lines])}
            items = ", ".join(f"{names.get(l['product_id'], '?')} ×{l['quantity']}" for l in r.lines)
            who = r.requester_location.rep.username if r.requester_location.rep_id else r.requester_location.branch.name
            dest = ("Stock Requests", reverse("production:stock_requests")) if has_production_access and not is_owner else (
                ("Order Stock", reverse("sales:stock_request_create")) if user.role == "SALES_REP" else (None, None))
            add(r.created_at.date(), "stock_request", "Stock Request", r.pk, "", who, "Production", items,
                r.status, r.get_status_display(), dest[0], dest[1])

    # ---- Approval: sale reversal, swap, refund... requested by staff at a branch ----
    pa_qs = PendingAction.objects.filter(business=biz).select_related("requested_by")
    if not is_owner:
        pa_qs = pa_qs.filter(requested_by__branch=user.branch) if user.role == "MANAGER" else pa_qs.filter(requested_by=user)
    for a in pa_qs.order_by("-created_at")[:60]:
        dest = ("Approvals", reverse("manager:approvals")) if user.role == "MANAGER" else (None, None)
        add(a.created_at.date(), "approval", "Approval", a.pk, "", a.requested_by.username, "",
            a.get_action_type_display(), a.status, a.get_status_display(), dest[0], dest[1])

    # ---- Batch Produced: a completed production run — Owner's window into
    # the factory floor, same "informational, not a second inbox" rule as
    # the branch-scoped kinds above ----
    if is_owner:
        from production.models import ProductionBatch
        batch_qs = ProductionBatch.objects.filter(business=biz, status="COMPLETED").select_related("branch", "product")
        for b in batch_qs.order_by("-manufacture_date", "-id")[:60]:
            add(b.manufacture_date or b.date, "batch", "Batch Produced", b.pk, b.batch_number,
                b.branch.name if b.branch else "—", b.product.name,
                f"{b.actual_quantity} units @ UGX {b.unit_cost_at_production:,.2f}/unit",
                b.status, b.get_status_display(), "Batch Report", reverse("reports:production_batch_detail", args=[b.pk]))

    # ---- Transaction: anything posted to the ledger — every sale, expense,
    # purchase, payment, reversal or manual entry that touches the books ----
    if is_owner:
        from finance.models import JournalEntry
        je_qs = JournalEntry.objects.filter(business=biz).select_related("branch").prefetch_related("lines")
        for e in je_qs.order_by("-date", "-id")[:60]:
            total = sum(l.debit for l in e.lines.all())
            add(e.date, "transaction", "Transaction", e.pk, e.number or e.source_ref, e.get_source_display(),
                e.branch.name if e.branch else "—", f"UGX {total:,.2f} — {e.memo}", "POSTED", "Posted",
                "General Ledger", reverse("finance:general_ledger"))

    visible_kinds = {e["kind_code"] for e in events}
    kind_choices = [(code, label) for code, label in _NOTIF_KIND_LABELS if code in visible_kinds]

    type_filter = request.GET.get("type", "")
    if type_filter:
        events = [e for e in events if e["kind_code"] == type_filter]
    date_from = request.GET.get("from", "")
    date_to = request.GET.get("to", "")
    if date_from:
        events = [e for e in events if str(e["date"]) >= date_from]
    if date_to:
        events = [e for e in events if str(e["date"]) <= date_to]
    events.sort(key=lambda e: e["date"], reverse=True)

    page_obj = Paginator(events, 15).get_page(request.GET.get("page"))
    qd = request.GET.copy()
    qd.pop("page", None)
    return render(request, "notifications.html", {
        "page_obj": page_obj, "extra_qs": qd.urlencode(), "date_from": date_from, "date_to": date_to,
        "type_filter": type_filter, "kind_choices": kind_choices,
    })


@login_required
def notification_go(request, kind, pk):
    """Where every notification's action link actually goes through — records
    the personal dismissal, then sends them on to the real destination."""
    from .models import NotificationDismissal
    NotificationDismissal.objects.get_or_create(user=request.user, kind=kind, object_id=pk)
    next_url = request.GET.get("next", "")
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        next_url = "home"
    return redirect(next_url)


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
        new_username = request.POST.get("username", "").strip()
        if not new_username:
            return render(request, "accounts/user_edit.html", {
                "target": target, "available_modules": available_modules, "tier_choices": PRICE_TIERS,
                "error": "Username can't be blank.",
            })
        if User.objects.filter(username=new_username).exclude(pk=target.pk).exists():
            return render(request, "accounts/user_edit.html", {
                "target": target, "available_modules": available_modules, "tier_choices": PRICE_TIERS,
                "error": f'"{new_username}" is already taken by another account.',
            })

        target.username = new_username
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
