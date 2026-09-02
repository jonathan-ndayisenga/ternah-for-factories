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
               ("SWAP", "Swap"), ("REVERSAL", "Reversal")]
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
