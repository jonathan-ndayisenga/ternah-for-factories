"""The manager's own home — a views-only app, same role `reports` plays for
the owner: no models of its own, just a branch-locked lens over Product
(production), Sale/Debtor/PendingAction/StockItem (sales). Every query here
is scoped to request.user.branch — a manager never sees another branch."""
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db.models import F
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.models import Branch
from finance.services import reverse_sale
from production.models import Distribution, Product
from sales.models import (
    Debtor, DebtorPayment, DebtorPaymentAllocation, InventoryLocation, OutletTransfer, PendingAction, Sale,
    StockItem, StockMovement, StockReturn, StockReturnLine,
)

manager_required = user_passes_test(lambda u: u.is_authenticated and u.role == "MANAGER")
manager_or_owner_required = user_passes_test(lambda u: u.is_authenticated and u.role in ("MANAGER", "OWNER"))


def _outlet(request):
    return InventoryLocation.objects.filter(branch=request.user.branch, type="OUTLET").first()


# ---------- Home (see the Home Redesign developer spec) ----------

_HOME_ICONS = {
    "home": '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/>',
    "production": '<path d="M2 22V10l5 3V10l5 3V10l5 3V4h5v18z"/><path d="M2 22h20"/>',
    "branch": '<path d="M20.5 7.3 12 12l-8.5-4.7M12 22V12"/><path d="m20.5 16.7-8.5 4.7-8.5-4.7V7.3L12 2.6l8.5 4.7z"/>',
    "cashier": '<circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"/>',
    "finance": '<rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2"/><path d="M6 12h.01M18 12h.01"/>',
    "staff": '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
}


def _has_production_access(user):
    return "PRODUCTION" in user.switchable_views()


def _home_tile_defs(user):
    """One entry per possible tile, in mockup order. `available` gates
    whether it's shown at all (Production only for a manager who's been
    granted that module — same rule the sidebar/switcher already use)."""
    from django.urls import reverse
    return [
        {"key": "production", "span": 2, "title": "Production", "icon": _HOME_ICONS["production"],
         "href": reverse("production:dashboard"), "available": _has_production_access(user),
         "actions": [{"label": "Start batch", "href": reverse("production:batch_create")},
                     {"label": "Formulas", "href": reverse("production:product_list")},
                     {"label": "Distribution", "href": reverse("production:distributions")}]},
        {"key": "branch", "span": 2, "title": "Branch & stock", "icon": _HOME_ICONS["branch"],
         "href": reverse("manager:dashboard"), "available": True,
         "actions": [{"label": "Review approvals", "href": reverse("manager:approvals")},
                     {"label": "Inventory", "href": reverse("manager:inventory")},
                     {"label": "Branch dashboard", "href": reverse("manager:dashboard")}]},
        {"key": "cashier", "span": 2, "title": "Cashier", "icon": _HOME_ICONS["cashier"],
         "href": reverse("sales:pos"), "available": True,
         "actions": [{"label": "Open till", "href": reverse("sales:pos")},
                     {"label": "Today's sales", "href": reverse("sales:pos")}]},
        {"key": "finance", "span": 1, "title": "Finance", "icon": _HOME_ICONS["finance"],
         "href": reverse("manager:debtors"), "available": True,
         "actions": [{"label": "Debtors", "href": reverse("manager:debtors")},
                     {"label": "Cashbook", "href": reverse("finance:cashbook")}]},
        {"key": "staff", "span": 1, "title": "Staff", "icon": _HOME_ICONS["staff"],
         "href": reverse("user_list"), "available": True,
         "actions": [{"label": "Staff list", "href": reverse("user_list")},
                     {"label": "Roles", "href": reverse("user_list")}]},
    ]


def _home_tile_number(request, key):
    """The stat(s) for a single tile (plus warn state) — shared by the
    first server-rendered paint and the /home/tiles/<tile>/ HTMX refresh.
    Production is the one tile with two stats side by side (batch count is
    never itself a problem, so only the shortage figure ever carries warn)."""
    from manager import home_metrics as hm
    user = request.user
    if key == "production":
        batches = hm.production_batches(request)
        short = hm.formulas_short_of_materials(request)
        return {"warn": bool(short), "stats": [
            {"number": batches, "unit_label": f"batch{'es' if batches != 1 else ''} in progress", "warn": False},
            {"number": short, "unit_label": f"formula{'s' if short != 1 else ''} short of materials",
             "warn": bool(short),
             "warn_label": f"{short} formula{'s' if short != 1 else ''} short of materials, needs attention" if short else ""},
        ]}
    if key == "branch":
        count = hm.branch_stock_awaiting_approval(user)
        return {"warn": False, "stats": [{"number": count, "unit_label": "stock movements awaiting approval", "warn": False}]}
    if key == "cashier":
        amount = hm.sales_today(user)
        return {"warn": False, "stats": [{"number": f"UGX {amount:,.0f}", "unit_label": "sales today", "warn": False}]}
    if key == "finance":
        count = hm.debtors_owing(user)
        noun = "debtor" if count == 1 else "debtors"
        return {"warn": count > 0, "stats": [{"number": count, "unit_label": f"{noun} overdue", "warn": count > 0,
                "warn_label": f"{count} {noun} overdue, needs attention" if count else ""}]}
    if key == "staff":
        return {"warn": False, "empty_text": "Nothing needs your attention"}
    return {"warn": False, "stats": []}


@login_required
@manager_required
def home(request):
    """The manager's Home — a live status board, one number per module.
    See the Home Redesign developer spec. Scoped to the manager role only;
    every other role keeps the existing tile picker (home_tiles.html).

    The sidebar shows no module links here — same "pick a tile to get
    started" idiom the rest of the app already uses on its own Home screen
    (see base.html). Clicking a tile is what takes you into that module's
    own nav (its existing screens already show their own items plus a Home
    link back to here); Home itself is a launchpad, not a nav level."""
    from django.urls import reverse
    u = request.user
    tiles = []
    for tile_def in _home_tile_defs(u):
        if not tile_def["available"]:
            continue
        tile = dict(tile_def)
        tile["poll_url"] = reverse("home_tile", args=[tile["key"]])
        tile.update(_home_tile_number(request, tile["key"]))
        tiles.append(tile)

    hour = timezone.localtime().hour
    period = "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"
    factory_name = u.business.name if u.business else "Ternah for Factories"
    return render(request, "manager/home.html", {
        "tiles": tiles, "factory_name": factory_name, "greeting_period": period,
    })


@login_required
@manager_required
def home_tile(request, tile):
    """GET /home/tiles/<tile>/ — returns only the number fragment for one
    tile, polled every 30s by the tile's own hx-get. Cached per tenant per
    tile for 30s so a page full of tiles, refreshing together, doesn't
    multiply into a query storm."""
    from django.core.cache import cache
    tile_def = next((t for t in _home_tile_defs(request.user) if t["key"] == tile and t["available"]), None)
    if tile_def is None:
        from django.http import Http404
        raise Http404
    cache_key = f"home_tile:{request.user.business_id}:{tile}"
    numbers = cache.get(cache_key)
    if numbers is None:
        numbers = _home_tile_number(request, tile)
        cache.set(cache_key, numbers, 30)
    merged = dict(tile_def)
    merged.update(numbers)
    return render(request, "ternah_ui/tile_body.html", {"tile": merged})


@login_required
@manager_required
def home_search(request):
    """GET /home/search/?q=... — products, batches, customers (debtors) and
    receipts, this tenant only. Small, capped result sets per group; empty
    query returns nothing rather than a full listing."""
    from django.db.models import Q
    from django.http import HttpResponse
    from django.urls import reverse
    q = request.GET.get("q", "").strip()
    if not q:
        # Truly empty — not even whitespace — so .tui-search-results:empty
        # in CSS actually hides the box. A template render here always
        # leaves stray newlines behind, which defeats that selector and
        # shows a blank floating panel the moment the field gets focus.
        return HttpResponse("")

    biz, branch = request.user.business, request.user.branch
    groups = []

    # Catalog — shared business-wide, not branch-specific.
    products = Product.objects.filter(business=biz, name__icontains=q)[:6]
    if products:
        groups.append({"label": "Products", "rows": [
            {"text": p.name, "href": reverse("production:formula_edit", args=[p.pk])} for p in products]})

    # Everything below is scoped to this manager's own branch — a search
    # box is still an access boundary, not just a filter.
    from production.models import ProductionBatch
    batches = ProductionBatch.objects.filter(business=biz, branch=branch, batch_number__icontains=q)[:6]
    if batches:
        groups.append({"label": "Batches", "rows": [
            {"text": b.batch_number, "href": reverse("reports:production_batch_detail", args=[b.pk])} for b in batches]})

    customers = Debtor.objects.filter(
        business=biz, location__branch=branch
    ).filter(Q(name__icontains=q) | Q(phone__icontains=q))[:6]
    if customers:
        groups.append({"label": "Customers", "rows": [
            {"text": f"{d.name}{' — ' + d.phone if d.phone else ''}",
             "href": reverse("manager:debtor_detail", args=[d.pk])} for d in customers]})

    receipts = Sale.objects.filter(
        business=biz, location__branch=branch
    ).filter(Q(receipt_number__icontains=q) | Q(customer_name__icontains=q))[:6]
    if receipts:
        groups.append({"label": "Receipts", "rows": [
            {"text": f"{s.receipt_number} — {s.customer_name or 'Walk-in'}",
             "href": reverse("sales:receipt", args=[s.pk])} for s in receipts]})

    from accounts.models import User
    from accounts.views import MANAGER_CREATABLE_ROLES
    staff = User.objects.filter(
        business=biz, branch=branch, role__in=MANAGER_CREATABLE_ROLES
    ).filter(Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q))[:6]
    if staff:
        groups.append({"label": "Staff", "rows": [
            {"text": f"{s.get_full_name() or s.username} — {s.get_role_display()}",
             "href": reverse("user_edit", args=[s.pk])} for s in staff]})

    if "PRODUCTION" in request.user.switchable_views():
        from production.models import RawMaterial
        materials = RawMaterial.objects.filter(business=biz, name__icontains=q)[:6]
        if materials:
            groups.append({"label": "Raw Materials", "rows": [
                {"text": m.name, "href": reverse("production:raw_material_movements", args=[m.pk])} for m in materials]})

    return render(request, "ternah_ui/_search_results.html", {"groups": groups, "q": q})


@login_required
@manager_required
def dashboard(request):
    biz, branch = request.user.business, request.user.branch
    context = {
        "pending_approvals": PendingAction.objects.filter(
            business=biz, status="PENDING", requested_by__branch=branch).count(),
        "awaiting_distributions": Distribution.objects.filter(
            business=biz, status="SENT", receiver_location__branch=branch).count(),
        "awaiting_pricing": Product.objects.filter(business=biz, status="DRAFT").count(),
        "low_stock": StockItem.objects.filter(
            location__branch=branch, quantity__lte=F("low_stock_threshold")).count(),
    }
    return render(request, "manager/dashboard.html", context)


@login_required
@manager_required
def debtors(request):
    branch = request.user.branch
    today = timezone.localdate()
    rows = []
    for d in Debtor.objects.filter(business=request.user.business, location__branch=branch).select_related("location"):
        balance = d.balance()
        if balance <= 0:
            continue
        oldest = d.sales.filter(balance__gt=0).order_by("created_at").first()
        age_days = (today - oldest.created_at.date()).days if oldest else 0
        rows.append({"debtor": d, "balance": balance, "age_days": age_days})
    rows.sort(key=lambda r: -r["age_days"])
    page_obj = Paginator(rows, 25).get_page(request.GET.get("page"))
    for row in page_obj:
        row["idempotency_key"] = uuid.uuid4().hex
    return render(request, "manager/debtors.html", {"page_obj": page_obj})


@login_required
@manager_required
def debtor_detail(request, pk):
    """Every item this debtor has ever taken on credit, and every payment
    they've made — kept visible even once fully paid, not just while owing."""
    debtor = get_object_or_404(Debtor, pk=pk, business=request.user.business, location__branch=request.user.branch)
    sales = debtor.sales.prefetch_related("items__product").order_by("-created_at")
    payments = DebtorPayment.objects.filter(debtor=debtor).select_related("received_by").order_by("-created_at")
    return render(request, "manager/debtor_detail.html", {
        "debtor": debtor, "sales": sales, "payments": payments, "balance": debtor.balance(),
    })


@login_required
@manager_required
def approvals(request):
    branch = request.user.branch
    actions = PendingAction.objects.filter(
        business=request.user.business, status="PENDING", requested_by__branch=branch
    ).select_related("requested_by").order_by("created_at")
    for action in actions:
        if action.action_type == "RETURN_TO_PRODUCTION":
            items = ", ".join(f"{l['product_name']} ×{l['quantity']}" for l in action.payload.get("lines", []))
            factory = Branch.objects.filter(pk=action.payload.get("factory_branch_id")).first()
            action.payload_display = [("Items", items), ("To", factory.name if factory else "—")]
            if action.payload.get("note"):
                action.payload_display.append(("Note", action.payload["note"]))
        else:
            action.payload_display = [(k.replace("_", " ").title(), v) for k, v in action.payload.items() if k != "sale_id"]
    return render(request, "manager/approvals.html", {"actions": actions})


def _execute_sale_reversal(action, manager):
    """The real effect, only run once a manager (never whoever rang up the
    sale) approves: stock physically goes back on the shelf, and the books
    get a proper reversing entry — nothing about the original sale is
    edited or deleted, matching every other reversal in this app."""
    sale = Sale.objects.filter(pk=action.payload.get("sale_id"), business=action.business).select_related("location").first()
    if not sale:
        return False, "That sale no longer exists."
    if sale.is_reversed:
        return False, "This sale was already reversed."
    if sale.payment_method == "OUTLET_TRANSFER":
        return False, "An outlet transfer can't be reversed this way."
    if sale.debtor_id and DebtorPaymentAllocation.objects.filter(sale=sale).exists():
        return False, "A payment has already landed against this sale's debt — that needs sorting out first."

    for item in sale.items.select_related("product"):
        stock_item, _ = StockItem.objects.get_or_create(
            location=sale.location, product=item.product, defaults={"buying_price": item.unit_cost, "selling_price": 0})
        stock_item.quantity += item.quantity
        stock_item.save(update_fields=["quantity"])
        StockMovement.objects.create(location=sale.location, product=item.product, quantity=item.quantity,
                                     reason="RETURN", reference=sale.receipt_number, moved_by=manager,
                                     counterparty_name=sale.customer_name)
    reverse_sale(sale, actor=manager)
    sale.is_reversed = True
    if sale.payment_method == "CREDIT":
        sale.balance = 0
    sale.save(update_fields=["is_reversed", "balance"])
    return True, f"{sale.receipt_number} reversed — stock is back and the books have a correcting entry."


def _execute_return_to_production(action, manager):
    """The real effect, only once a manager approves: stock actually leaves
    the rep's own inventory and a real StockReturn starts its trip to the
    factory — still SENT, not landed, until Production confirms receipt on
    their own end, same in-transit safety as every other stock-moving flow
    here. Nothing about the rep's stock changes just from asking."""
    payload = action.payload
    location = InventoryLocation.objects.filter(pk=payload.get("from_location_id"), business=action.business).first()
    factory_store = InventoryLocation.objects.filter(
        business=action.business, branch_id=payload.get("factory_branch_id"), type="PRODUCTION_STORE").first()
    if not location or not factory_store:
        return False, "That rep's inventory or the factory store no longer exists."

    lines = []
    for l in payload.get("lines", []):
        item = StockItem.objects.filter(location=location, product_id=l["product_id"]).first()
        if not item:
            continue
        qty = min(int(l["quantity"]), item.quantity)   # can't take more than they actually still hold by now
        if qty > 0:
            lines.append((item, qty))
    if not lines:
        return False, "None of the requested stock is still on hand at that location."

    seq = StockReturn.objects.filter(business=action.business, date=timezone.localdate()).count() + 1
    ret = StockReturn.objects.create(
        business=action.business, from_location=location, to_location=factory_store,
        reference_number=f"RET-{action.requested_by.username[:4].upper()}-{timezone.localdate():%d%m%y}-{seq:03d}",
        date=timezone.localdate(), status="SENT", created_by=action.requested_by,
        note=payload.get("note", ""),
    )
    for item, qty in lines:
        StockReturnLine.objects.create(stock_return=ret, product=item.product, quantity=qty)
        item.quantity -= qty
        item.save(update_fields=["quantity"])
        StockMovement.objects.create(location=location, product=item.product, quantity=-qty,
                                     reason="RETURN", reference=ret.reference_number,
                                     moved_by=manager, counterparty=factory_store)
    return True, f"Approved — {ret.reference_number} is on its way to {factory_store.branch.name}, pending their confirmation."


@login_required
@manager_required
def approval_decide(request, pk):
    action = get_object_or_404(PendingAction, pk=pk, business=request.user.business,
                               status="PENDING", requested_by__branch=request.user.branch)
    if request.method == "POST":
        decision = request.POST.get("decision")
        if decision == "approve":
            if action.action_type == "SALE_REVERSAL":
                ok, msg = _execute_sale_reversal(action, request.user)
                if not ok:
                    messages.error(request, msg)
                    return redirect("manager:approvals")
                messages.success(request, msg)
            elif action.action_type == "RETURN_TO_PRODUCTION":
                ok, msg = _execute_return_to_production(action, request.user)
                if not ok:
                    messages.error(request, msg)
                    return redirect("manager:approvals")
                messages.success(request, msg)
            action.status = "APPROVED"
        elif decision == "reject":
            action.status = "REJECTED"
            action.reject_reason = request.POST.get("reject_reason", "").strip()
        action.reviewed_by = request.user
        action.save()
    return redirect("manager:approvals")


@login_required
@manager_required
def inventory(request):
    location = _outlet(request)
    context = {
        "location": location,
        "incoming": Distribution.objects.filter(
            business=request.user.business, status="SENT", receiver_location__branch=request.user.branch
        ).prefetch_related("lines__product").order_by("date"),
        "incoming_transfers": OutletTransfer.objects.filter(
            business=request.user.business, status="SENT", to_location__branch=request.user.branch
        ).select_related("from_location__branch").prefetch_related("lines__product").order_by("date"),
        "incoming_returns": StockReturn.objects.filter(
            business=request.user.business, status__in=["SENT", "DISPUTED"], to_location__branch=request.user.branch
        ).select_related("from_location__rep").prefetch_related("lines__product").order_by("date"),
    }
    if location:
        items = list(StockItem.objects.filter(location=location).select_related("product").order_by("product__name"))
        for i in items:
            i.value = i.quantity * i.buying_price
        context.update({
            "items": items,
            "stock_value": sum((i.value for i in items), 0),
        })
    return render(request, "manager/inventory.html", context)


@login_required
@manager_required
def return_to_production(request):
    """The branch's own outlet sending stock straight to Production —
    damaged goods, a batch that shouldn't have landed here. Manager-
    initiated, so it's immediate — no approval step, unlike a rep's own
    RETURN_TO_PRODUCTION request, since the manager already IS the
    approving authority for their own branch. Still SENT, not landed,
    until Production confirms receipt — same in-transit safety as every
    other stock-moving flow here."""
    biz = request.user.business
    location = _outlet(request)
    factories = Branch.objects.filter(business=biz, kind="FACTORY", is_active=True).order_by("name")
    items = list(StockItem.objects.filter(location=location, quantity__gt=0).select_related("product").order_by("product__name")) \
        if location else []

    if request.method == "POST" and location:
        idempotency_key = request.POST.get("idempotency_key", "").strip()
        if idempotency_key and StockReturn.objects.filter(business=biz, idempotency_key=idempotency_key).exists():
            messages.success(request, "That return was already sent.")
            return redirect("manager:return_to_production")
        factory = factories.filter(pk=request.POST.get("factory")).first() or factories.first()
        factory_store = InventoryLocation.objects.filter(business=biz, branch=factory, type="PRODUCTION_STORE").first() \
            if factory else None
        if not factory_store:
            messages.error(request, "There's no factory finished-goods store set up on this business yet.")
            return redirect("manager:return_to_production")
        lines = []
        for pid, qty in zip(request.POST.getlist("product"), request.POST.getlist("quantity")):
            try:
                qty = int(qty)
            except (TypeError, ValueError):
                continue
            if not pid or qty <= 0:
                continue
            item = StockItem.objects.filter(location=location, product_id=pid).first()
            if not item:
                continue
            qty = min(qty, item.quantity)   # can't return more than actually on hand
            if qty <= 0:
                continue
            lines.append((item, qty))
        if lines:
            seq = StockReturn.objects.filter(business=biz, date=timezone.localdate()).count() + 1
            ret = StockReturn.objects.create(
                business=biz, from_location=location, to_location=factory_store,
                reference_number=f"RET-{request.user.username[:4].upper()}-{timezone.localdate():%d%m%y}-{seq:03d}",
                date=timezone.localdate(), status="SENT", created_by=request.user,
                note=request.POST.get("note", "").strip(), idempotency_key=idempotency_key,
            )
            for item, qty in lines:
                StockReturnLine.objects.create(stock_return=ret, product=item.product, quantity=qty)
                item.quantity -= qty
                item.save(update_fields=["quantity"])
                StockMovement.objects.create(location=location, product=item.product, quantity=-qty,
                                             reason="RETURN", reference=ret.reference_number,
                                             moved_by=request.user, counterparty=factory_store)
            messages.success(request, f"Sent to {factory.name} — reference {ret.reference_number}. "
                                      f"It'll land in their store once Production confirms.")
        else:
            messages.error(request, "Add at least one product with a quantity to return.")
        return redirect("manager:return_to_production")

    my_returns = StockReturn.objects.filter(from_location=location, to_location__type="PRODUCTION_STORE") \
        .prefetch_related("lines__product").order_by("-date", "-id")[:20] if location else []
    return render(request, "manager/return_to_production.html", {
        "location": location, "items": items, "factories": factories, "my_returns": my_returns,
        "idempotency_key": uuid.uuid4().hex,
    })


@login_required
@manager_or_owner_required
def stock_movements(request):
    """Every unit that moved, in or out — sale, distribution, batch landing —
    with who moved it and, for distributions, who the other side was.
    Branch-locked for a manager, business-wide for the owner."""
    biz = request.user.business
    qs = StockMovement.objects.filter(location__business=biz).select_related(
        "product", "location__branch", "location__rep",
        "counterparty__branch", "counterparty__rep", "moved_by",
    ).order_by("-created_at")
    if request.user.role == "MANAGER":
        qs = qs.filter(location__branch=request.user.branch)

    page_obj = Paginator(qs, 25).get_page(request.GET.get("page"))
    for m in page_obj:
        if m.counterparty:
            m.flow_label = f"Sent to {m.counterparty}" if m.quantity < 0 else f"Received from {m.counterparty}"
        elif m.counterparty_name:
            m.flow_label = f"Sold to {m.counterparty_name}"
        else:
            m.flow_label = None
    print_title = "Stock Movements" + (f" — {request.user.branch.name}" if request.user.role == "MANAGER" else "")
    return render(request, "manager/stock_movements.html", {"page_obj": page_obj, "print_title": print_title})
