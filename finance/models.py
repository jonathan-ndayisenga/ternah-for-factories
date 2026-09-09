from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from core.models import TimeStamped


class Supplier(TimeStamped):
    business = models.ForeignKey("platformadmin.Business", on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=30, blank=True)


class SupplierPayable(TimeStamped):
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name="payables")
    description = models.CharField(max_length=200)
    total_amount = models.DecimalField(max_digits=16, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=[("OPEN", "Open"), ("PAID", "Paid")], default="OPEN")


class InvoiceClient(TimeStamped):
    """A curated billing relationship — not every walk-in customer, just the
    ones invoiced on negotiated terms (e.g. a supermarket buying at custom
    pricing on account)."""
    business = models.ForeignKey("platformadmin.Business", on_delete=models.CASCADE, related_name="invoice_clients")
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, blank=True)
    tin = models.CharField(max_length=30, blank=True)
    address = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Invoice(TimeStamped):
    """Two-directional: AR is a bill we raise to a client (they owe us),
    AP is a bill we record from a supplier (we owe them)."""
    DIRECTIONS = [("AR", "Bill to client"), ("AP", "Bill from supplier")]
    business = models.ForeignKey("platformadmin.Business", on_delete=models.CASCADE, related_name="invoices")
    branch = models.ForeignKey("core.Branch", null=True, blank=True, on_delete=models.SET_NULL)
    direction = models.CharField(max_length=2, choices=DIRECTIONS)
    number = models.CharField(max_length=40, blank=True)      # INV{INITIALS}-DDMMYY-SEQ
    client = models.ForeignKey(InvoiceClient, null=True, blank=True, on_delete=models.PROTECT, related_name="invoices")
    supplier = models.ForeignKey(Supplier, null=True, blank=True, on_delete=models.PROTECT, related_name="invoices")
    issue_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    total = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    amount_paid = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    notes = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    is_void = models.BooleanField(default=False)

    def balance(self):
        return self.total - self.amount_paid

    def status(self):
        if self.is_void:
            return "VOID"
        if self.amount_paid <= 0:
            return "UNPAID"
        if self.amount_paid >= self.total:
            return "PAID"
        return "PARTIAL"


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    description = models.CharField(max_length=200)
    quantity = models.DecimalField(max_digits=12, decimal_places=3, default=1)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    line_total = models.DecimalField(max_digits=16, decimal_places=2)


class InvoicePayment(TimeStamped):
    METHODS = [("CASH", "Cash"), ("MOBILE_MONEY", "Mobile Money"), ("BANK", "Bank")]
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    method = models.CharField(max_length=15, choices=METHODS)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)


class FinancialAccount(TimeStamped):
    """Assignable to users. The manager treasury disburses to everyone."""
    business = models.ForeignKey("platformadmin.Business", on_delete=models.CASCADE)
    name = models.CharField(max_length=80)
    assigned_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    is_manager_treasury = models.BooleanField(default=False)


class Disbursement(TimeStamped):
    from_account = models.ForeignKey(FinancialAccount, on_delete=models.PROTECT, related_name="disbursements_out")
    to_account = models.ForeignKey(FinancialAccount, on_delete=models.PROTECT, related_name="disbursements_in")
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    note = models.CharField(max_length=200, blank=True)
    number = models.CharField(max_length=40, blank=True)      # DISB{INITIALS}-DDMMYY-SEQ


class LedgerAccount(models.Model):
    """Ternah six account types."""
    TYPES = [("ASSET", "Asset"), ("LIABILITY", "Liability"), ("EQUITY", "Equity"),
             ("INCOME", "Income"), ("EXPENSE", "Expense"), ("COGS", "Cost of goods sold")]
    business = models.ForeignKey("platformadmin.Business", on_delete=models.CASCADE)
    code = models.CharField(max_length=10)
    name = models.CharField(max_length=80)
    type = models.CharField(max_length=10, choices=TYPES)

    class Meta:
        unique_together = [("business", "code")]


class JournalEntry(TimeStamped):
    """Immutable; corrections are reversals."""
    SOURCES = [("SALE", "Sale"), ("CREDIT_SALE", "Credit sale"), ("COLLECTION", "Collection"),
               ("RM_PURCHASE", "Raw material purchase"), ("BATCH", "Production batch"),
               ("EXPENSE", "Expense"), ("DISBURSEMENT", "Disbursement"), ("PAYABLE_PAYMENT", "Payable payment"),
               ("ORDER_DEPOSIT", "Order deposit"), ("REMITTANCE", "Rep remittance"),
               ("SWAP", "Swap"), ("REVERSAL", "Reversal"), ("CAPITAL", "Owner capital"),
               ("OUTLET_TRANSFER_OUT", "Outlet transfer (sent)"), ("OUTLET_TRANSFER_IN", "Outlet transfer (received)"),
               ("COGS_CORRECTION", "Cost of goods sold — correction"),
               ("INVOICE_AR", "Invoice raised to client"), ("INVOICE_AR_PAYMENT", "Invoice payment received"),
               ("INVOICE_AP", "Supplier bill recorded"), ("INVOICE_AP_PAYMENT", "Supplier bill paid"),
               ("MANUAL", "Manual journal entry")]
    business = models.ForeignKey("platformadmin.Business", on_delete=models.CASCADE)
    branch = models.ForeignKey("core.Branch", null=True, blank=True, on_delete=models.SET_NULL)  # branch P&L
    number = models.CharField(max_length=40, blank=True)      # JNL{INITIALS}-DDMMYY-SEQ
    date = models.DateField()
    memo = models.CharField(max_length=200, blank=True)
    source = models.CharField(max_length=20, choices=SOURCES)
    source_ref = models.CharField(max_length=60, blank=True)
    posted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)

    def clean(self):
        d = sum(l.debit for l in self.lines.all())
        c = sum(l.credit for l in self.lines.all())
        if d != c:
            raise ValidationError("Journal entry is not balanced: debits != credits")


class JournalLine(models.Model):
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    ledger_account = models.ForeignKey(LedgerAccount, on_delete=models.PROTECT)
    debit = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=16, decimal_places=2, default=0)
