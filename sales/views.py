"""Point of sale — identical shape at an outlet (cashier) or in a rep's own
inventory (sales rep): today's sales, ring up a sale, log an expense, collect
a debtor payment. Every sale records who it was sold to, cash or credit."""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from accounts.models import PRICE_TIERS
from finance.services import post_debtor_payment, post_expense, post_outlet_transfer_expense, post_sale
from production.models import Distribution, DistributionLine, Product
from .models import (
    BankAccount, DailyOpeningBalance, Debtor, DebtorPayment, Expense, InventoryLocation,
    MomoAccount, OutletTransfer, OutletTransferLine, Sale, SaleItem, StockItem, StockMovement, StockRequest,
)

POS_ROLES = ("CASHIER", "SALES_REP")
pos_required = user_passes_test(lambda u: u.is_authenticated and (u.role in POS_ROLES or u.role == "MANAGER"))
ALL_TIER_CODES = [code for code, _ in PRICE_TIERS]


def _acting_role(request):
    return request.active_view if request.user.role == "MANAGER" else request.user.role


def _pos_location(request):
    """A sales rep sells out of their own personal inventory; everyone else
    (cashier, or a manager previewing a view) sells out of their branch's outlet."""
    if _acting_role(request) == "SALES_REP":
        loc = getattr(request.user, "inventory", None)
        if loc:
            return loc
    return InventoryLocation.objects.filter(branch=request.user.branch, type="OUTLET").first()


def _allowed_tiers(request):
    """The manager sets everyone else's tiers and is the only one who can use
    a custom/negotiated price — so when the manager themself is at the POS
    (via the view switcher), they get every tier, not just what they'd have
    granted a cashier or rep."""
    if request.user.role == "MANAGER":
        return ALL_TIER_CODES
    return request.user.allowed_tiers or []


def _tier_available(product, tier):
    if tier == "CUSTOM":
        return product.custom_price_min is not None and product.custom_price_max is not None
    return product.price_for_tier(tier) is not None


@login_required
@pos_required
def pos(request):
    location = _pos_location(request)
    allowed_tiers = _allowed_tiers(request)
    momo_accounts = MomoAccount.objects.filter(business=request.user.business, is_active=True)
    bank_accounts = BankAccount.objects.filter(business=request.user.business, is_active=True)
    context = {
        "location": location, "tier_labels": dict(PRICE_TIERS), "allowed_tiers": allowed_tiers,
        "momo_accounts": momo_accounts, "bank_accounts": bank_accounts,
    }
    if location:
        today = timezone.localdate()
        opening_balance = DailyOpeningBalance.objects.filter(location=location, date=today).select_related("recorded_by").first()
        context["opening_balance"] = opening_balance
        today_sales = Sale.objects.filter(location=location, created_at__date=today).order_by("-created_at")
        debtors = [d for d in Debtor.objects.filter(location=location) if d.balance() > 0]
        today_expenses = Expense.objects.filter(location=location, date=today).select_related("recorded_by").order_by("-created_at")
        stock_items = StockItem.objects.filter(location=location, quantity__gt=0).select_related("product")
        # only offer products priced for at least one tier this seller is allowed to use
        sellable = []
        product_data = {}
        for i in stock_items:
            i.available_tiers = [t for t in allowed_tiers if _tier_available(i.product, t)]
            if not i.available_tiers:
                continue
            sellable.append(i)
            product_data[str(i.product_id)] = {
                "prices": {t: float(i.product.price_for_tier(t)) for t in i.available_tiers if t != "CUSTOM"},
                "custom_min": float(i.product.custom_price_min) if i.product.custom_price_min is not None else None,
                "custom_max": float(i.product.custom_price_max) if i.product.custom_price_max is not None else None,
                "stock": i.quantity,
            }
        qd_base = request.GET.copy()
        for key in ("sales_page", "debtors_page", "expenses_page", "panel"):
            qd_base.pop(key, None)
        qd_sales = qd_base.copy(); qd_sales["panel"] = "sales"
        qd_debtors = qd_base.copy(); qd_debtors["panel"] = "debtors"
        qd_expenses = qd_base.copy(); qd_expenses["panel"] = "expenses"
        sales_page = Paginator(today_sales, 15).get_page(request.GET.get("sales_page"))
        debtors_page = Paginator(debtors, 15).get_page(request.GET.get("debtors_page"))
        expenses_page = Paginator(today_expenses, 15).get_page(request.GET.get("expenses_page"))

        context.update({
            "stock_items": sellable,
            "product_data": product_data,
            "sales_page_obj": sales_page,
            "debtors_page_obj": debtors_page,
            "expenses_page_obj": expenses_page,
            "sales_extra_qs": qd_sales.urlencode(),
            "debtors_extra_qs": qd_debtors.urlencode(),
            "expenses_extra_qs": qd_expenses.urlencode(),
            "today_total": sum((s.total for s in today_sales), Decimal("0")),
            "debtor_total": sum((d.balance() for d in debtors), Decimal("0")),
            "expense_total": sum((e.amount for e in today_expenses), Decimal("0")),
            "open_panel": request.GET.get("panel", ""),
        })
        if location.type == "OUTLET":
            context["destination_outlets"] = InventoryLocation.objects.filter(
                business=request.user.business, type="OUTLET").exclude(pk=location.pk).select_related("branch")
    return render(request, "sales/pos.html", context)


@login_required
@pos_required
def record_sale(request):
    location = _pos_location(request)
    if request.method != "POST" or not location:
        return redirect("sales:pos")

    customer_name = request.POST.get("customer_name", "").strip()
    method = request.POST.get("payment_method")
    if method not in dict(Sale.METHODS):
        return redirect("sales:pos")
    if method == "CREDIT" and not customer_name:
        return redirect("sales:pos")   # can't track a debt with nobody's name on it

    biz = request.user.business
    momo_account = None
    if method == "MOBILE_MONEY":
        momo_account = MomoAccount.objects.filter(
            pk=request.POST.get("momo_account"), business=biz, is_active=True).first()
        if not momo_account:
            return redirect("sales:pos")   # no mobile money account set up/selected — nothing to record against

    bank_account = None
    if method == "CARD":
        bank_account = BankAccount.objects.filter(
            pk=request.POST.get("bank_account"), business=biz, is_active=True).first()
        if not bank_account:
            return redirect("sales:pos")   # no bank account set up/selected — nothing to record against

    to_location = None
    if method == "OUTLET_TRANSFER":
        if location.type != "OUTLET":
            messages.error(request, "Only an outlet can send stock to another outlet.")
            return redirect("sales:pos")
        to_location = InventoryLocation.objects.filter(
            pk=request.POST.get("to_location"), business=biz, type="OUTLET").exclude(pk=location.pk).first()
        if not to_location:
            messages.error(request, "Pick a destination outlet to send stock to.")
            return redirect("sales:pos")
        customer_name = f"{to_location.branch.name} (outlet transfer)"

        # guard rail: can never send more than this outlet actually has on
        # the shelf — checked as a hard error here, not silently clamped,
        # since it's the same reference number the receiving outlet's books
        # will pick up as an expense, so it needs to be exactly right
        requested = {}
        for pid, qty in zip(request.POST.getlist("product"), request.POST.getlist("quantity")):
            try:
                qty = int(qty)
            except (TypeError, ValueError):
                continue
            if pid and qty > 0:
                requested[pid] = requested.get(pid, 0) + qty
        for pid, total_qty in requested.items():
            item = StockItem.objects.filter(location=location, product_id=pid).select_related("product").first()
            if item and total_qty > item.quantity:
                messages.error(request, f"Can't send {total_qty} of {item.product.name} "
                                        f"— only {item.quantity} available in your stock.")
                return redirect("sales:pos")

    allowed = _allowed_tiers(request)
    lines, subtotal = [], Decimal("0")
    for pid, qty, tier, custom_price_raw in zip(
        request.POST.getlist("product"), request.POST.getlist("quantity"),
        request.POST.getlist("tier"), request.POST.getlist("custom_price"),
    ):
        try:
            qty = int(qty)
        except (TypeError, ValueError):
            continue
        if not pid or qty <= 0 or tier not in allowed:
            continue
        item = StockItem.objects.filter(location=location, product_id=pid).select_related("product").first()
        if not item:
            continue

        if tier == "CUSTOM":
            cmin, cmax = item.product.custom_price_min, item.product.custom_price_max
            if cmin is None or cmax is None:
                continue   # custom pricing not configured for this product
            try:
                unit_price = Decimal(custom_price_raw)
            except InvalidOperation:
                continue
            if unit_price < cmin or unit_price > cmax:
                continue   # outside the manager's allowed range
        else:
            unit_price = item.product.price_for_tier(tier)
            if unit_price is None:
                continue   # not priced for this tier — nothing to sell it at

        qty = min(qty, item.quantity)
        if qty <= 0:
            continue
        line_total = (unit_price * qty).quantize(Decimal("0.01"))
        lines.append((item, qty, unit_price, line_total))
        subtotal += line_total
    if not lines:
        return redirect("sales:pos")

    debtor = None
    amount_paid, balance = subtotal, Decimal("0")
    if method == "CREDIT":
        debtor, _ = Debtor.objects.get_or_create(
            business=biz, location=location, name=customer_name,
            defaults={"phone": request.POST.get("customer_phone", "").strip()},
        )
        amount_paid, balance = Decimal("0"), subtotal

    customer_phone = request.POST.get("customer_phone", "").strip()
    seq = Sale.objects.filter(location=location, created_at__date=timezone.localdate()).count() + 1
    receipt = f"RCP-{timezone.localdate():%d%m%y}-{location.id}{seq:03d}"
    sale = Sale.objects.create(
        business=biz, location=location, receipt_number=receipt,
        served_by=request.user, acted_as=_acting_role(request) if request.user.role == "MANAGER" else "",
        payment_method=method, customer_name=customer_name, customer_phone=customer_phone,
        paid_into_momo=momo_account, paid_into_bank=bank_account,
        debtor=debtor, subtotal=subtotal, total=subtotal, amount_paid=amount_paid, balance=balance,
    )
    transfer = None
    movement_reference = receipt
    movement_counterparty_name = ""
    if method == "OUTLET_TRANSFER":
        seq2 = OutletTransfer.objects.filter(business=biz, date=timezone.localdate()).count() + 1
        transfer = OutletTransfer.objects.create(
            business=biz, from_location=location, to_location=to_location,
            reference_number=f"TRF-{request.user.username[:4].upper()}-{timezone.localdate():%d%m%y}-{seq2:03d}",
            date=timezone.localdate(), status="SENT", created_by=request.user, sale=sale,
        )
        movement_reference = transfer.reference_number   # shared reference across both legs of the transfer
    elif customer_name:
        movement_counterparty_name = f"{customer_name} ({customer_phone})" if customer_phone else customer_name

    for item, qty, unit_price, line_total in lines:
        SaleItem.objects.create(sale=sale, product=item.product, quantity=qty,
                                unit_price=unit_price, unit_cost=item.buying_price, line_total=line_total)
        item.quantity -= qty
        item.save(update_fields=["quantity"])
        StockMovement.objects.create(
            location=location, product=item.product, quantity=-qty,
            reason="TRANSFER_OUT" if method == "OUTLET_TRANSFER" else "SALE",
            reference=movement_reference, moved_by=request.user,
            counterparty=to_location if method == "OUTLET_TRANSFER" else None,
            counterparty_name=movement_counterparty_name,
        )
        if transfer:
            OutletTransferLine.objects.create(transfer=transfer, product=item.product, quantity=qty)
    post_sale(sale)

    if transfer:
        messages.success(request, f"Sent to {to_location.branch.name} — reference {transfer.reference_number}. "
                                  f"It'll land in their inventory once they confirm receipt.")
    return redirect(f"{reverse('sales:receipt', args=[sale.pk])}?auto_print=1")


@login_required
@pos_required
def outlet_transfers(request):
    """A cashier/rep's own view of stock they've sent to, or received from,
    another outlet — with status, so 'sent but not yet confirmed' is visible
    without having to ask the other side."""
    location = _pos_location(request)
    sent = received = []
    if location:
        sent = list(OutletTransfer.objects.filter(from_location=location)
                    .select_related("to_location__branch").prefetch_related("lines__product")
                    .order_by("-date", "-id"))
        received = list(OutletTransfer.objects.filter(to_location=location)
                        .select_related("from_location__branch").prefetch_related("lines__product")
                        .order_by("-date", "-id"))
    return render(request, "sales/outlet_transfers.html", {"location": location, "sent": sent, "received": received})


@login_required
@user_passes_test(lambda u: u.is_authenticated and u.role in ("OWNER", "MANAGER"))
def outlet_transfer_confirm(request, pk):
    """Receiver's side of the loop: stock only actually lands in their
    inventory once they confirm, and only then does the matching expense
    post to their books — same in-transit safety as production.Distribution.
    Owner/Manager only, same precedent as confirming a Distribution."""
    next_url = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        next_url = "manager:inventory"

    biz = request.user.business
    transfer = get_object_or_404(OutletTransfer, pk=pk, business=biz, status="SENT")
    if request.user.role == "MANAGER" and transfer.to_location.branch_id != request.user.branch_id:
        return redirect(next_url)

    action = request.POST.get("action")
    if action == "confirm":
        for line in transfer.lines.select_related("product"):
            # carry the sender's current cost basis over — this becomes the
            # receiving outlet's buying price for this product, same as
            # every other place stock lands (see production.batch_complete
            # and production.distribution_confirm)
            source_cost = StockItem.objects.filter(
                location=transfer.from_location, product=line.product).values_list("buying_price", flat=True).first() or 0
            item, _ = StockItem.objects.get_or_create(
                location=transfer.to_location, product=line.product,
                defaults={"buying_price": source_cost, "selling_price": 0},
            )
            item.quantity += line.quantity
            item.buying_price = source_cost
            item.save(update_fields=["quantity", "buying_price"])
            StockMovement.objects.create(location=transfer.to_location, product=line.product, quantity=line.quantity,
                                         reason="TRANSFER_IN", reference=transfer.reference_number,
                                         moved_by=request.user, counterparty=transfer.from_location)
        expense = Expense.objects.create(
            business=biz, location=transfer.to_location, category="Inter-Outlet Stock Transfer",
            amount=transfer.sale.total if transfer.sale else 0,
            note=f"From {transfer.from_location.branch.name} — ref {transfer.reference_number}",
            date=timezone.localdate(), recorded_by=request.user,
        )
        post_outlet_transfer_expense(expense)
        transfer.status = "RECEIVED"
        transfer.confirmed_by = request.user
        transfer.expense = expense
        transfer.save(update_fields=["status", "confirmed_by", "expense"])
    elif action == "dispute":
        transfer.status = "DISPUTED"
        transfer.save(update_fields=["status"])
    return redirect(next_url)


@login_required
@pos_required
def record_expense(request):
    location = _pos_location(request)
    if request.method == "POST" and location:
        category = request.POST.get("category", "").strip()
        try:
            amount = Decimal(request.POST.get("amount", ""))
        except InvalidOperation:
            amount = None
        if category and amount and amount > 0:
            expense = Expense.objects.create(
                business=request.user.business, location=location, category=category,
                amount=amount, note=request.POST.get("note", "").strip(),
                date=timezone.localdate(), recorded_by=request.user)
            post_expense(expense)
    return redirect(f"{reverse('sales:pos')}?panel=expenses")


@login_required
@pos_required
def record_opening_balance(request):
    """Whoever opens up today counts the till and records it here — one per
    location per day, editable (not locked) in case of a miscount or a
    manager correcting it later."""
    location = _pos_location(request)
    if request.method == "POST" and location:
        try:
            amount = Decimal(request.POST.get("amount", ""))
        except InvalidOperation:
            amount = None
        if amount is not None and amount >= 0:
            DailyOpeningBalance.objects.update_or_create(
                location=location, date=timezone.localdate(),
                defaults={"business": request.user.business, "amount": amount, "recorded_by": request.user},
            )
    return redirect("sales:pos")


@login_required
@pos_required
def received_items(request):
    """A rep's own view of what's landed in their inventory over time — date,
    product, quantity, and the price they're meant to sell it at (their
    manager-assigned tiers, priced per the manager's product pricing)."""
    location = _pos_location(request)
    allowed = _allowed_tiers(request)
    lines = []
    if location:
        lines = list(DistributionLine.objects.filter(distribution__receiver_location=location,
                                                      distribution__status="RECEIVED")
                     .select_related("product", "distribution", "batch").order_by("-distribution__date"))
        for line in lines:
            line.your_prices = [
                (label, line.product.price_for_tier(code))
                for code, label in PRICE_TIERS
                if code in allowed and code != "CUSTOM" and line.product.price_for_tier(code) is not None
            ]
    return render(request, "sales/received_items.html", {"location": location, "lines": lines})


@login_required
@user_passes_test(lambda u: u.is_authenticated and u.role == "SALES_REP")
def stock_request_create(request):
    """A rep asking Production for more stock — shows what's currently
    sitting in the factory store so the request is grounded in reality, but
    doesn't hard-block asking for more (stock moves between now and when
    Production actually looks at it). Lands in Production's Stock Requests
    inbox; fulfilling it there is what actually creates the distribution."""
    location = _pos_location(request)
    biz = request.user.business
    factory_store = InventoryLocation.objects.filter(business=biz, type="PRODUCTION_STORE").first()
    active_products = Product.objects.filter(business=biz, status="ACTIVE").order_by("name")
    stock_by_product = {
        item.product_id: item.quantity
        for item in StockItem.objects.filter(location=factory_store, product__in=active_products)
    } if factory_store else {}
    for p in active_products:
        p.available_at_factory = stock_by_product.get(p.id, 0)

    if request.method == "POST" and location:
        lines = []
        for pid, qty in zip(request.POST.getlist("product"), request.POST.getlist("quantity")):
            try:
                qty = int(qty)
            except (TypeError, ValueError):
                continue
            if not pid or qty <= 0:
                continue
            if not active_products.filter(pk=pid).exists():
                continue
            lines.append({"product_id": int(pid), "quantity": qty, "fulfilled": 0, "dropped": 0})
        mode = request.POST.get("fulfillment_mode")
        mode = mode if mode in dict(StockRequest.MODES) else "WAIT"
        if lines:
            StockRequest.objects.create(business=biz, requester_location=location, status="SUBMITTED",
                                        fulfillment_mode=mode, lines=lines)
            messages.success(request, "Stock request sent to Production.")
        else:
            messages.error(request, "Add at least one product with a quantity to request.")
        return redirect("sales:stock_request_create")

    my_requests = StockRequest.objects.filter(requester_location=location).order_by("-created_at")[:20] if location else []
    for r in my_requests:
        r.lines_display = [
            {**l, "product": active_products.filter(pk=l["product_id"]).first() or
                             Product.objects.filter(pk=l["product_id"]).first()}
            for l in r.line_progress()
        ]
    return render(request, "sales/stock_request_create.html", {
        "location": location, "active_products": active_products, "my_requests": my_requests,
        "mode_choices": StockRequest.MODES,
    })


@login_required
@pos_required
def record_payment(request):
    """Reached from two places: the POS's own compact debtor list (scoped to
    that exact location) and the manager's branch-wide Debtors page (which can
    include a rep's debtors too, not just the outlet's) — so a manager not
    currently acting as that rep still needs to be able to pay one down."""
    next_url = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        next_url = "sales:pos"
    if request.method != "POST":
        return redirect(next_url)

    if request.user.role == "MANAGER" and _acting_role(request) == "MANAGER":
        debtor = Debtor.objects.filter(pk=request.POST.get("debtor_id"), business=request.user.business,
                                       location__branch=request.user.branch).first()
    else:
        location = _pos_location(request)
        debtor = Debtor.objects.filter(pk=request.POST.get("debtor_id"), location=location).first() if location else None

    try:
        amount = Decimal(request.POST.get("amount", ""))
    except InvalidOperation:
        amount = None
    if debtor and amount and amount > 0:
        payment = DebtorPayment.objects.create(debtor=debtor, amount=amount, method="CASH", received_by=request.user)
        post_debtor_payment(payment)
        remaining = amount
        for open_sale in debtor.sales.filter(balance__gt=0).order_by("created_at"):
            if remaining <= 0:
                break
            applied = min(remaining, open_sale.balance)
            open_sale.balance -= applied
            open_sale.amount_paid += applied
            open_sale.save(update_fields=["balance", "amount_paid"])
            remaining -= applied
    return redirect(next_url)


def _can_view_location(request, location):
    """Same scoping rule everywhere a receipt might be opened: owner sees
    everything, manager their own branch, cashier/rep only their own till."""
    if request.user.role == "OWNER":
        return True
    if request.user.role == "MANAGER":
        return location.branch_id == request.user.branch_id
    if request.user.role in POS_ROLES:
        return location.id == getattr(_pos_location(request), "id", None)
    return False


@login_required
def receipt(request, pk):
    sale = get_object_or_404(Sale, pk=pk, business=request.user.business)
    if not _can_view_location(request, sale.location):
        raise Http404
    return render(request, "sales/receipt.html", {
        "sale": sale, "items": sale.items.select_related("product"),
        "print_title": f"Receipt {sale.receipt_number}",
    })


@login_required
def debtor_payment_receipt(request, pk):
    payment = get_object_or_404(DebtorPayment, pk=pk, debtor__business=request.user.business)
    if not _can_view_location(request, payment.debtor.location):
        raise Http404
    return render(request, "sales/debtor_payment_receipt.html", {
        "payment": payment, "debtor": payment.debtor, "balance_after": payment.debtor.balance(),
        "print_title": f"Payment Receipt — {payment.debtor.name}",
    })
