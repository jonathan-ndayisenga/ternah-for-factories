"""Finance, for the manager (and owner): payment accounts the business
collects into, a branch cashbook, an activity feed, and the real accounting
layer — general ledger, trial balance, P&L, balance sheet, revenue by
product, expense journal. Everything in the ledger is posted by
finance.services as the underlying business events happen; nothing here
writes to JournalEntry/JournalLine directly."""
from datetime import datetime as dt
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

PAGE_SIZE = 25


def _paginate(request, items, page_size=PAGE_SIZE):
    page_obj = Paginator(items, page_size).get_page(request.GET.get("page"))
    qd = request.GET.copy()
    qd.pop("page", None)
    return page_obj, qd.urlencode()

from core.models import Branch
from production.models import Distribution, RawMaterialPurchase
from sales.models import BankAccount, DailyOpeningBalance, DebtorPayment, Expense, MomoAccount, PendingAction, Sale, SaleItem
from .models import JournalEntry, JournalLine, LedgerAccount
from .services import get_accounts, post_capital_transaction

finance_staff_required = user_passes_test(lambda u: u.is_authenticated and u.role in ("OWNER", "MANAGER"))
owner_required = user_passes_test(lambda u: u.is_authenticated and u.role == "OWNER")


def _as_datetime(value):
    """Expense only carries a date (no time); every other event has a real
    datetime. Normalize so templates can format them uniformly."""
    if isinstance(value, dt):
        return value
    return timezone.make_aware(dt.combine(value, dt.min.time()))


@login_required
@finance_staff_required
def payment_accounts(request):
    biz = request.user.business
    if request.method == "POST":
        kind = request.POST.get("kind")
        if kind == "momo":
            provider = request.POST.get("provider", "").strip()
            number = request.POST.get("number", "").strip()
            if provider and number:
                MomoAccount.objects.create(business=biz, provider=provider, number=number)
        elif kind == "bank":
            bank_name = request.POST.get("bank_name", "").strip()
            account_name = request.POST.get("account_name", "").strip()
            account_number = request.POST.get("account_number", "").strip()
            if bank_name and account_name and account_number:
                BankAccount.objects.create(business=biz, bank_name=bank_name,
                                           account_name=account_name, account_number=account_number)
        return redirect("finance:payment_accounts")
    return render(request, "finance/payment_accounts.html", {
        "momo_accounts": MomoAccount.objects.filter(business=biz).order_by("-is_active", "provider"),
        "bank_accounts": BankAccount.objects.filter(business=biz).order_by("-is_active", "bank_name"),
    })


@login_required
@finance_staff_required
def payment_account_toggle(request, kind, pk):
    biz = request.user.business
    model = MomoAccount if kind == "momo" else BankAccount
    account = model.objects.filter(pk=pk, business=biz).first()
    if account and request.method == "POST":
        account.is_active = not account.is_active
        account.save(update_fields=["is_active"])
    return redirect("finance:payment_accounts")


@login_required
@finance_staff_required
def cashbook(request):
    """Branch-locked for a manager; business-wide (every branch) for the owner —
    same convention as reports._branch_filter."""
    biz = request.user.business
    branch_filter = {"location__branch": request.user.branch} if request.user.role == "MANAGER" else {}
    debtor_branch_filter = {"debtor__location__branch": request.user.branch} if request.user.role == "MANAGER" else {}

    opening_balances = DailyOpeningBalance.objects.filter(
        business=biz, date=timezone.localdate(), **branch_filter
    ).select_related("location__branch", "recorded_by")

    entries = []
    for s in Sale.objects.filter(business=biz, amount_paid__gt=0, **branch_filter).select_related("location", "served_by"):
        entries.append({"sort_date": s.created_at.date(), "when": s.created_at, "type": "Sale",
                        "ref": s.receipt_number, "method": s.get_payment_method_display(), "amount": s.amount_paid,
                        "by": s.served_by, "receipt_url": reverse("sales:receipt", args=[s.pk])})
    for p in DebtorPayment.objects.filter(debtor__business=biz, **debtor_branch_filter).select_related("debtor", "received_by"):
        entries.append({"sort_date": p.created_at.date(), "when": p.created_at, "type": "Debt Collection",
                        "ref": p.debtor.name, "method": p.method, "amount": p.amount, "by": p.received_by,
                        "receipt_url": reverse("sales:debtor_payment_receipt", args=[p.pk])})
    for e in Expense.objects.filter(business=biz, **branch_filter).select_related("recorded_by"):
        entries.append({"sort_date": e.date, "when": _as_datetime(e.date), "type": "Expense",
                        "ref": e.category, "method": "—", "amount": -e.amount, "by": e.recorded_by})
    if request.user.role == "OWNER":
        # raw material purchases aren't tied to any one branch — they only
        # belong in the business-wide (Owner) cashbook, and only the ones
        # actually paid in cash; on-credit ones haven't touched cash yet
        for rm in RawMaterialPurchase.objects.filter(raw_material__business=biz, on_credit=False) \
                .select_related("raw_material", "recorded_by"):
            entries.append({"sort_date": rm.purchase_date, "when": _as_datetime(rm.purchase_date),
                            "type": "RM Purchase", "ref": rm.raw_material.name, "method": "Cash",
                            "amount": -rm.total_cost, "by": rm.recorded_by})

    entries.sort(key=lambda x: (x["sort_date"], x["type"]))
    running = Decimal("0")
    for entry in entries:
        running += entry["amount"]
        entry["running"] = running
    entries.reverse()   # most recent first for display; running balance already computed forward
    closing_balance = running

    page_obj, extra_qs = _paginate(request, entries)
    print_title = "Cashbook" + (f" — {request.user.branch.name}" if request.user.role == "MANAGER" else "")
    return render(request, "finance/cashbook.html", {
        "page_obj": page_obj, "extra_qs": extra_qs, "closing_balance": closing_balance,
        "opening_balances": opening_balances, "print_title": print_title,
    })


@login_required
@finance_staff_required
def journal(request):
    """Everything that happened at the branch, not just cash — stock received
    from production, credit taken on and paid down, swaps/refunds decided —
    the full activity trail the cashbook (cash only) doesn't show."""
    biz = request.user.business
    branch = None if request.user.role == "OWNER" else request.user.branch
    entries = []

    sales_qs = Sale.objects.filter(business=biz).select_related("served_by")
    if branch:
        sales_qs = sales_qs.filter(location__branch=branch)
    for s in sales_qs:
        who = s.customer_name or "a walk-in customer"
        entries.append({
            "sort_date": s.created_at.date(), "when": s.created_at, "type": "Sale",
            "description": f"{s.receipt_number} to {who} — {s.get_payment_method_display()}",
            "amount": s.total, "by": s.served_by,
        })

    payments_qs = DebtorPayment.objects.filter(debtor__business=biz).select_related("debtor", "received_by")
    if branch:
        payments_qs = payments_qs.filter(debtor__location__branch=branch)
    for p in payments_qs:
        entries.append({
            "sort_date": p.created_at.date(), "when": p.created_at, "type": "Debt Payment",
            "description": f"{p.debtor.name} paid down their balance", "amount": p.amount, "by": p.received_by,
        })

    dist_qs = Distribution.objects.filter(business=biz, status="RECEIVED") \
        .select_related("created_by", "confirmed_by", "receiver_location__branch", "receiver_location__rep") \
        .prefetch_related("lines__product")
    if branch:
        dist_qs = dist_qs.filter(receiver_location__branch=branch)
    for d in dist_qs:
        items = ", ".join(f"{l.product.name} ×{l.quantity}" for l in d.lines.all())
        sent_by = d.created_by.username if d.created_by else "—"
        entries.append({
            "sort_date": d.updated_at.date(), "when": d.updated_at, "type": "Stock Received",
            "description": (f"{d.delivery_note_number}: {items} — sent by {sent_by} to {d.receiver_location}"
                            if items else f"{d.delivery_note_number} — sent by {sent_by} to {d.receiver_location}"),
            "amount": None, "by": d.confirmed_by,
        })

    actions_qs = PendingAction.objects.filter(business=biz).exclude(status="PENDING") \
        .select_related("requested_by", "reviewed_by")
    if branch:
        actions_qs = actions_qs.filter(requested_by__branch=branch)
    for a in actions_qs:
        entries.append({
            "sort_date": a.updated_at.date(), "when": a.updated_at, "type": a.get_action_type_display(),
            "description": f"{a.get_status_display()} — requested by {a.requested_by.username}"
                           + (f" ({a.reject_reason})" if a.reject_reason else ""),
            "amount": None, "by": a.reviewed_by,
        })

    expenses_qs = Expense.objects.filter(business=biz).select_related("recorded_by")
    if branch:
        expenses_qs = expenses_qs.filter(location__branch=branch)
    for e in expenses_qs:
        entries.append({
            "sort_date": e.date, "when": _as_datetime(e.date), "type": "Expense",
            "description": f"{e.category}" + (f" — {e.note}" if e.note else ""),
            "amount": -e.amount, "by": e.recorded_by,
        })

    if not branch:   # raw material purchases aren't branch-scoped — Owner's activity feed only
        for rm in RawMaterialPurchase.objects.filter(raw_material__business=biz).select_related("raw_material", "supplier", "recorded_by"):
            paid = f"on credit from {rm.supplier.name}" if rm.on_credit and rm.supplier else "paid in cash"
            entries.append({
                "sort_date": rm.purchase_date, "when": _as_datetime(rm.purchase_date), "type": "RM Purchase",
                "description": f"{rm.quantity} {rm.raw_material.unit_of_measure} of {rm.raw_material.name} — {paid}",
                "amount": -rm.total_cost, "by": rm.recorded_by,
            })

    entries.sort(key=lambda x: x["sort_date"], reverse=True)
    page_obj, extra_qs = _paginate(request, entries)
    print_title = "Activity" + (f" — {branch.name}" if branch else "")
    return render(request, "finance/journal.html", {"page_obj": page_obj, "extra_qs": extra_qs, "print_title": print_title})


@login_required
@finance_staff_required
def general_ledger(request):
    """The real double-entry journal — every posted entry, debits and
    credits, in order. Branch-locked for a manager, business-wide for owner."""
    biz = request.user.business
    entries = JournalEntry.objects.filter(business=biz).prefetch_related("lines__ledger_account") \
        .order_by("-date", "-id")
    if request.user.role == "MANAGER":
        entries = entries.filter(branch=request.user.branch)
    page_obj, extra_qs = _paginate(request, entries, page_size=15)
    print_title = "General Ledger" + (f" — {request.user.branch.name}" if request.user.role == "MANAGER" else "")
    return render(request, "finance/general_ledger.html", {"page_obj": page_obj, "extra_qs": extra_qs, "print_title": print_title})


@login_required
@finance_staff_required
def expense_journal(request):
    biz = request.user.business
    qs = Expense.objects.filter(business=biz).select_related("recorded_by", "location__branch")
    if request.user.role == "MANAGER":
        qs = qs.filter(location__branch=request.user.branch)
    date_from = request.GET.get("from", "")
    date_to = request.GET.get("to", "")
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    qs = qs.order_by("-date", "-id")
    total = qs.aggregate(s=Sum("amount"))["s"] or Decimal("0")
    by_category = qs.values("category").annotate(total=Sum("amount")).order_by("-total")
    page_obj, extra_qs = _paginate(request, qs)
    print_title = "Expense Journal" + (f" — {request.user.branch.name}" if request.user.role == "MANAGER" else "")
    return render(request, "finance/expense_journal.html", {
        "page_obj": page_obj, "extra_qs": extra_qs, "total": total, "by_category": by_category,
        "date_from": date_from, "date_to": date_to, "print_title": print_title,
    })


def _account_balance(balances, account):
    """Positive = the account's normal healthy side (Dr for asset/expense/
    COGS, Cr for liability/equity/income) — so every figure downstream in
    the statements reads as a plain positive number, not a signed ledger net."""
    d, c = balances.get(account.id, (Decimal("0"), Decimal("0")))
    if account.type in ("ASSET", "EXPENSE", "COGS"):
        return d - c
    return c - d


@login_required
@owner_required
def financial_reports(request):
    """Trial Balance, Profit & Loss (both optionally date-ranged) and Balance
    Sheet (always as-of-now — a balance sheet is a snapshot, not a period) —
    plus Revenue by Product. Owner-only: these are whole-business statements,
    not a branch's operational view."""
    biz = request.user.business
    accounts = get_accounts(biz)
    date_from = request.GET.get("from", "")
    date_to = request.GET.get("to", "")
    branch_id = request.GET.get("branch", "")
    selected_branch = Branch.objects.filter(pk=branch_id, business=biz).first() if branch_id else None

    def balances_for(qs):
        agg = qs.values("ledger_account_id").annotate(d=Sum("debit"), c=Sum("credit"))
        return {row["ledger_account_id"]: (row["d"] or Decimal("0"), row["c"] or Decimal("0")) for row in agg}

    period_lines = JournalLine.objects.filter(entry__business=biz)
    if date_from:
        period_lines = period_lines.filter(entry__date__gte=date_from)
    if date_to:
        period_lines = period_lines.filter(entry__date__lte=date_to)
    if selected_branch:
        # entries with no branch (raw material purchases, batch completions,
        # owner capital) aren't any one outlet's — they simply drop out of a
        # per-outlet trial balance, which is correct: each remaining entry
        # is still fully self-contained, so debits still equal credits
        period_lines = period_lines.filter(entry__branch=selected_branch)
    period_balances = balances_for(period_lines)
    all_time_balances = balances_for(JournalLine.objects.filter(entry__business=biz))

    # ---- Trial balance (period) ----
    trial_rows, total_debit, total_credit = [], Decimal("0"), Decimal("0")
    for acct in LedgerAccount.objects.filter(business=biz).order_by("code"):
        d, c = period_balances.get(acct.id, (Decimal("0"), Decimal("0")))
        net = d - c
        dr, cr = (net, Decimal("0")) if net >= 0 else (Decimal("0"), -net)
        if dr or cr:
            trial_rows.append({"account": acct, "debit": dr, "credit": cr})
        total_debit += dr
        total_credit += cr

    # ---- Profit & Loss (period) ----
    revenue = _account_balance(period_balances, accounts["4000"])
    cogs = _account_balance(period_balances, accounts["5000"])
    opex = _account_balance(period_balances, accounts["5100"])
    gross_profit = revenue - cogs
    net_profit = gross_profit - opex

    # ---- Balance sheet (as-of-now, all-time) ----
    cash = _account_balance(all_time_balances, accounts["1000"])
    momo = _account_balance(all_time_balances, accounts["1010"])
    bank = _account_balance(all_time_balances, accounts["1020"])
    receivable = _account_balance(all_time_balances, accounts["1100"])
    rm_inventory = _account_balance(all_time_balances, accounts["1200"])
    fg_inventory = _account_balance(all_time_balances, accounts["1210"])
    payable = _account_balance(all_time_balances, accounts["2000"])
    capital = _account_balance(all_time_balances, accounts["3000"])
    total_assets = cash + momo + bank + receivable + rm_inventory + fg_inventory
    retained_earnings = (
        _account_balance(all_time_balances, accounts["4000"])
        - _account_balance(all_time_balances, accounts["5000"])
        - _account_balance(all_time_balances, accounts["5100"])
    )

    # ---- Revenue by product (period; falls back to all-time if no sales yet posted for the range) ----
    item_qs = SaleItem.objects.filter(sale__business=biz)
    if date_from:
        item_qs = item_qs.filter(sale__created_at__date__gte=date_from)
    if date_to:
        item_qs = item_qs.filter(sale__created_at__date__lte=date_to)
    cost_expr = ExpressionWrapper(F("unit_cost") * F("quantity"), output_field=DecimalField(max_digits=16, decimal_places=2))
    product_rows = list(
        item_qs.values("product_id", "product__name")
        .annotate(qty=Sum("quantity"), revenue=Sum("line_total"), cost=Sum(cost_expr))
        .order_by("-revenue")
    )
    for row in product_rows:
        row["profit"] = row["revenue"] - (row["cost"] or Decimal("0"))
        row["margin_pct"] = (row["profit"] / row["revenue"] * 100) if row["revenue"] else Decimal("0")

    return render(request, "finance/financial_reports.html", {
        "date_from": date_from, "date_to": date_to,
        "branches": Branch.objects.filter(business=biz).order_by("name"), "selected_branch": selected_branch,
        "trial_rows": trial_rows, "total_debit": total_debit, "total_credit": total_credit,
        "revenue": revenue, "cogs": cogs, "gross_profit": gross_profit, "opex": opex, "net_profit": net_profit,
        "cash": cash, "momo": momo, "bank": bank, "receivable": receivable,
        "rm_inventory": rm_inventory, "fg_inventory": fg_inventory, "total_assets": total_assets,
        "payable": payable, "capital": capital, "retained_earnings": retained_earnings,
        "total_liabilities_equity": payable + capital + retained_earnings,
        "product_rows": product_rows, "print_title": "Financial Reports",
    })


@login_required
@owner_required
def record_capital(request):
    if request.method == "POST":
        try:
            amount = Decimal(request.POST.get("amount", ""))
        except InvalidOperation:
            amount = None
        into = request.POST.get("into") if request.POST.get("into") in ("cash", "bank") else "cash"
        if request.POST.get("direction") == "withdraw" and amount:
            amount = -amount
        if amount:
            post_capital_transaction(
                request.user.business, timezone.localdate(), amount, into,
                request.POST.get("memo", "").strip(), request.user)
    return redirect("finance:financial_reports")
