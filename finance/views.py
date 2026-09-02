"""Finance, for the manager (and owner): payment accounts the business
collects into, and a branch cashbook — every cash-in/cash-out event, in
order, with a running balance. Treasury/float accounts, disbursements and
payables are the next slice — not built yet, see the manager module plan."""
from datetime import datetime as dt
from decimal import Decimal

from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import redirect, render
from django.utils import timezone

from production.models import Distribution
from sales.models import BankAccount, DebtorPayment, Expense, MomoAccount, PendingAction, Sale

finance_staff_required = user_passes_test(lambda u: u.is_authenticated and u.role in ("OWNER", "MANAGER"))


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

    entries = []
    for s in Sale.objects.filter(business=biz, amount_paid__gt=0, **branch_filter).select_related("location"):
        entries.append({"sort_date": s.created_at.date(), "when": s.created_at, "type": "Sale",
                        "ref": s.receipt_number, "method": s.get_payment_method_display(), "amount": s.amount_paid})
    for p in DebtorPayment.objects.filter(debtor__business=biz, **debtor_branch_filter).select_related("debtor"):
        entries.append({"sort_date": p.created_at.date(), "when": p.created_at, "type": "Debt Collection",
                        "ref": p.debtor.name, "method": p.method, "amount": p.amount})
    for e in Expense.objects.filter(business=biz, **branch_filter):
        entries.append({"sort_date": e.date, "when": _as_datetime(e.date), "type": "Expense",
                        "ref": e.category, "method": "—", "amount": -e.amount})

    entries.sort(key=lambda x: (x["sort_date"], x["type"]))
    running = Decimal("0")
    for entry in entries:
        running += entry["amount"]
        entry["running"] = running
    entries.reverse()   # most recent first for display; running balance already computed forward

    return render(request, "finance/cashbook.html", {
        "entries": entries, "closing_balance": running,
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

    dist_qs = Distribution.objects.filter(business=biz, status="RECEIVED").select_related("created_by") \
        .prefetch_related("lines__product")
    if branch:
        dist_qs = dist_qs.filter(receiver_location__branch=branch)
    for d in dist_qs:
        items = ", ".join(f"{l.product.name} ×{l.quantity}" for l in d.lines.all())
        entries.append({
            "sort_date": d.updated_at.date(), "when": d.updated_at, "type": "Stock Received",
            "description": f"{d.delivery_note_number}: {items}" if items else d.delivery_note_number,
            "amount": None, "by": d.created_by,
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

    entries.sort(key=lambda x: x["sort_date"], reverse=True)
    return render(request, "finance/journal.html", {"entries": entries})
