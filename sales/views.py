"""Point of sale — identical shape at an outlet (cashier) or in a rep's own
inventory (sales rep): today's sales, ring up a sale, log an expense, collect
a debtor payment. Every sale records who it was sold to, cash or credit."""
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from accounts.models import PRICE_TIERS
from finance.services import post_debtor_payment, post_expense, post_sale
from production.models import Distribution, DistributionLine
from .models import (
    BankAccount, DailyOpeningBalance, Debtor, DebtorPayment, Expense, InventoryLocation,
    MomoAccount, Sale, SaleItem, StockItem, StockMovement,
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
            }
        qd_base = request.GET.copy()
        for key in ("sales_page", "debtors_page", "panel"):
            qd_base.pop(key, None)
        qd_sales = qd_base.copy(); qd_sales["panel"] = "sales"
        qd_debtors = qd_base.copy(); qd_debtors["panel"] = "debtors"
        sales_page = Paginator(today_sales, 15).get_page(request.GET.get("sales_page"))
        debtors_page = Paginator(debtors, 15).get_page(request.GET.get("debtors_page"))

        context.update({
            "stock_items": sellable,
            "product_data": product_data,
            "sales_page_obj": sales_page,
            "debtors_page_obj": debtors_page,
            "sales_extra_qs": qd_sales.urlencode(),
            "debtors_extra_qs": qd_debtors.urlencode(),
            "today_total": sum((s.total for s in today_sales), Decimal("0")),
            "debtor_total": sum((d.balance() for d in debtors), Decimal("0")),
            "open_panel": request.GET.get("panel", ""),
        })
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

    seq = Sale.objects.filter(location=location, created_at__date=timezone.localdate()).count() + 1
    receipt = f"RCP-{timezone.localdate():%d%m%y}-{location.id}{seq:03d}"
    sale = Sale.objects.create(
        business=biz, location=location, receipt_number=receipt,
        served_by=request.user, acted_as=_acting_role(request) if request.user.role == "MANAGER" else "",
        payment_method=method, customer_name=customer_name,
        customer_phone=request.POST.get("customer_phone", "").strip(),
        paid_into_momo=momo_account, paid_into_bank=bank_account,
        debtor=debtor, subtotal=subtotal, total=subtotal, amount_paid=amount_paid, balance=balance,
    )
    for item, qty, unit_price, line_total in lines:
        SaleItem.objects.create(sale=sale, product=item.product, quantity=qty,
                                unit_price=unit_price, unit_cost=item.buying_price, line_total=line_total)
        item.quantity -= qty
        item.save(update_fields=["quantity"])
        StockMovement.objects.create(location=location, product=item.product, quantity=-qty,
                                     reason="SALE", reference=receipt, moved_by=request.user)
    post_sale(sale)
    return redirect("sales:pos")


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
    return redirect("sales:pos")


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
                     .select_related("product", "distribution").order_by("-distribution__date"))
        for line in lines:
            line.your_prices = [
                (label, line.product.price_for_tier(code))
                for code, label in PRICE_TIERS
                if code in allowed and code != "CUSTOM" and line.product.price_for_tier(code) is not None
            ]
    return render(request, "sales/received_items.html", {"location": location, "lines": lines})


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
