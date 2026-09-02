"""The actual double-entry engine. Every business event that moves money or
inventory value posts one balanced JournalEntry here — sales, debtor
collections, expenses, raw material purchases, batch completions. Nothing
else in the app writes to LedgerAccount/JournalEntry/JournalLine directly.

Idempotent by design: each event gets a stable source_ref (e.g. "sale:42"),
and posting is skipped if that ref already has an entry — so calling a
post_* function twice (or backfilling over live data) never double-posts."""
from decimal import Decimal

from django.utils import timezone

from .models import JournalEntry, JournalLine, LedgerAccount

# Fixed chart of accounts — small and flat on purpose. Product/category-level
# detail comes from reports querying the source models directly (SaleItem,
# Expense), not from having a ledger account per product or per category.
ACCOUNT_DEFS = [
    ("1000", "Cash on Hand", "ASSET"),
    ("1010", "Mobile Money", "ASSET"),
    ("1020", "Bank", "ASSET"),
    ("1100", "Accounts Receivable", "ASSET"),
    ("1200", "Raw Materials Inventory", "ASSET"),
    ("1210", "Finished Goods Inventory", "ASSET"),
    ("2000", "Accounts Payable", "LIABILITY"),
    ("3000", "Owner's Equity — Capital", "EQUITY"),
    ("4000", "Sales Revenue", "INCOME"),
    ("5000", "Cost of Goods Sold", "COGS"),
    ("5100", "Operating Expenses", "EXPENSE"),
]

PAYMENT_ACCOUNT_CODES = {"CASH": "1000", "MOBILE_MONEY": "1010", "CARD": "1020"}


def get_accounts(business):
    """get_or_create the fixed chart for this business, keyed by code."""
    accounts = {}
    for code, name, type_ in ACCOUNT_DEFS:
        account, _ = LedgerAccount.objects.get_or_create(
            business=business, code=code, defaults={"name": name, "type": type_})
        accounts[code] = account
    return accounts


def _local_date(dt_or_date):
    if hasattr(dt_or_date, "date"):
        return timezone.localtime(dt_or_date).date()
    return dt_or_date


def _post(business, branch, date, source, source_ref, memo, lines, actor=None):
    """lines: [(account, debit, credit), ...]. Skips silently if source_ref
    already posted — makes every post_* function safe to call more than once."""
    if JournalEntry.objects.filter(business=business, source_ref=source_ref).exists():
        return None
    entry = JournalEntry.objects.create(
        business=business, branch=branch, date=date, memo=memo,
        source=source, source_ref=source_ref, posted_by=actor)
    for account, debit, credit in lines:
        if debit or credit:
            JournalLine.objects.create(entry=entry, ledger_account=account, debit=debit, credit=credit)
    return entry


def post_sale(sale):
    accounts = get_accounts(sale.business)
    lines = []
    if sale.payment_method == "CREDIT":
        lines.append((accounts["1100"], sale.total, 0))
        source = "CREDIT_SALE"
    else:
        pay_account = accounts[PAYMENT_ACCOUNT_CODES.get(sale.payment_method, "1000")]
        lines.append((pay_account, sale.total, 0))
        source = "SALE"
    lines.append((accounts["4000"], 0, sale.total))

    cogs_total = sum((i.unit_cost * i.quantity for i in sale.items.all()), Decimal("0")).quantize(Decimal("0.01"))
    if cogs_total:
        lines.append((accounts["5000"], cogs_total, 0))
        lines.append((accounts["1210"], 0, cogs_total))

    who = sale.customer_name or "a walk-in customer"
    return _post(sale.business, sale.location.branch, _local_date(sale.created_at), source,
                f"sale:{sale.pk}", f"{sale.receipt_number} to {who}", lines, actor=sale.served_by)


def post_debtor_payment(payment):
    business = payment.debtor.business
    accounts = get_accounts(business)
    pay_account = accounts[PAYMENT_ACCOUNT_CODES.get(payment.method, "1000")]
    return _post(business, payment.debtor.location.branch, _local_date(payment.created_at), "COLLECTION",
                f"debtor_payment:{payment.pk}", f"Payment from {payment.debtor.name}",
                [(pay_account, payment.amount, 0), (accounts["1100"], 0, payment.amount)],
                actor=payment.received_by)


def post_expense(expense):
    accounts = get_accounts(expense.business)
    memo = expense.category + (f" — {expense.note}" if expense.note else "")
    return _post(expense.business, expense.location.branch, expense.date, "EXPENSE",
                f"expense:{expense.pk}", memo,
                [(accounts["5100"], expense.amount, 0), (accounts["1000"], 0, expense.amount)],
                actor=expense.recorded_by)


def post_raw_material_purchase(purchase):
    business = purchase.raw_material.business
    accounts = get_accounts(business)
    credit_account = accounts["2000"] if purchase.on_credit else accounts["1000"]
    memo = f"{purchase.raw_material.name} — {purchase.batch_number or purchase.purchase_date}"
    return _post(business, None, purchase.purchase_date, "RM_PURCHASE", f"rm_purchase:{purchase.pk}", memo,
                [(accounts["1200"], purchase.total_cost, 0), (credit_account, 0, purchase.total_cost)])


def post_capital_transaction(business, date, amount, into, memo, actor):
    """The owner funding the business (or drawing money back out) — the only
    way Cash/Bank ever gets a balance with no sale or collection behind it.
    into: 'cash' or 'bank'. amount negative = a withdrawal, not a deposit."""
    accounts = get_accounts(business)
    target = accounts["1000"] if into == "cash" else accounts["1020"]
    ref = f"capital:{business.pk}:{timezone.now():%Y%m%d%H%M%S%f}"
    if amount >= 0:
        lines = [(target, amount, 0), (accounts["3000"], 0, amount)]
    else:
        lines = [(accounts["3000"], -amount, 0), (target, 0, -amount)]
    return _post(business, None, date, "CAPITAL", ref, memo or "Owner capital", lines, actor=actor)


def post_batch_completion(batch):
    accounts = get_accounts(batch.business)
    memo = f"{batch.batch_number} completed — {batch.actual_quantity} units"
    return _post(batch.business, None, batch.date, "BATCH", f"batch:{batch.pk}", memo,
                [(accounts["1210"], batch.total_cost, 0), (accounts["1200"], 0, batch.total_cost)])
