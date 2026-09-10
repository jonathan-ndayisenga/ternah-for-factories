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

from finance.services import reverse_sale
from production.models import Distribution, Product
from sales.models import (
    Debtor, DebtorPayment, DebtorPaymentAllocation, InventoryLocation, OutletTransfer, PendingAction, Sale,
    StockItem, StockMovement,
)

manager_required = user_passes_test(lambda u: u.is_authenticated and u.role == "MANAGER")
manager_or_owner_required = user_passes_test(lambda u: u.is_authenticated and u.role in ("MANAGER", "OWNER"))


def _outlet(request):
    return InventoryLocation.objects.filter(branch=request.user.branch, type="OUTLET").first()


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
