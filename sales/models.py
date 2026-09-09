from django.conf import settings
from django.db import models
from core.models import TimeStamped


class InventoryLocation(TimeStamped):
    """
    Everyone gets an IDENTICAL inventory shape.
    - Every branch has one (the factory branch's is the finished-goods store).
    - Every sales rep has a personal one, still tied to their branch,
      so rep stock/sales roll up under the branch in owner reports.
    """
    TYPE = [("PRODUCTION_STORE", "Finished goods store"), ("OUTLET", "Outlet"), ("REP", "Sales rep")]
    business = models.ForeignKey("platformadmin.Business", on_delete=models.CASCADE)
    branch = models.ForeignKey("core.Branch", on_delete=models.CASCADE, related_name="inventory_locations")
    type = models.CharField(max_length=20, choices=TYPE)
    rep = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True,
                               on_delete=models.CASCADE, related_name="inventory")

    def __str__(self):
        return f"{self.branch.name} / {self.rep or self.get_type_display()}"


class StockItem(models.Model):
    location = models.ForeignKey(InventoryLocation, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("production.Product", on_delete=models.PROTECT)
    quantity = models.IntegerField(default=0)
    buying_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)   # defaults to production unit cost
    selling_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)  # set by manager
    low_stock_threshold = models.IntegerField(default=0)

    class Meta:
        unique_together = [("location", "product")]


class StockMovement(TimeStamped):
    REASONS = [("DISTRIBUTION", "Distribution"), ("SALE", "Sale"), ("SWAP_IN", "Swap in"),
               ("SWAP_OUT", "Swap out"), ("ADJUSTMENT", "Adjustment"), ("RETURN", "Return"),
               ("TRANSFER_OUT", "Outlet transfer out"), ("TRANSFER_IN", "Outlet transfer in")]
    location = models.ForeignKey(InventoryLocation, on_delete=models.CASCADE, related_name="movements")
    product = models.ForeignKey("production.Product", on_delete=models.PROTECT)
    quantity = models.IntegerField()                 # signed
    reason = models.CharField(max_length=15, choices=REASONS)
    reference = models.CharField(max_length=60, blank=True)
    moved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    counterparty = models.ForeignKey(InventoryLocation, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="counterparty_movements")   # the other side of the move, if any
    counterparty_name = models.CharField(max_length=160, blank=True)   # free-text "to" for a walk-in customer sale


class MomoAccount(TimeStamped):
    business = models.ForeignKey("platformadmin.Business", on_delete=models.CASCADE)
    provider = models.CharField(max_length=40)
    number = models.CharField(max_length=30)
    is_active = models.BooleanField(default=True)


class BankAccount(TimeStamped):
    business = models.ForeignKey("platformadmin.Business", on_delete=models.CASCADE)
    bank_name = models.CharField(max_length=80)
    account_name = models.CharField(max_length=120)
    account_number = models.CharField(max_length=40)
    is_active = models.BooleanField(default=True)


class DailyOpeningBalance(TimeStamped):
    """What was physically counted in the till at the start of the day, per
    location — set by whoever opens up (cashier/rep), editable by a manager.
    Purely a reconciliation record: the cashbook's running balance is always
    computed from actual transactions, this is what gets compared against it
    to catch shrinkage, miscounts, or an unrecorded cash movement."""
    business = models.ForeignKey("platformadmin.Business", on_delete=models.CASCADE)
    location = models.ForeignKey(InventoryLocation, on_delete=models.CASCADE, related_name="opening_balances")
    date = models.DateField()
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)

    class Meta:
        unique_together = [("location", "date")]


class Debtor(TimeStamped):
    business = models.ForeignKey("platformadmin.Business", on_delete=models.CASCADE)
    location = models.ForeignKey(InventoryLocation, on_delete=models.PROTECT)    # which outlet/rep owns it
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=30, blank=True)

    def balance(self):
        owed = self.sales.aggregate(s=models.Sum("balance"))["s"] or 0
        return owed


class Sale(TimeStamped):
    """POS sale — the same shape at an outlet or in a rep's hands."""
    METHODS = [("CASH", "Cash"), ("CARD", "Card"), ("MOBILE_MONEY", "Mobile Money"), ("CREDIT", "Credit"),
               ("OUTLET_TRANSFER", "Outlet Transfer")]
    business = models.ForeignKey("platformadmin.Business", on_delete=models.CASCADE)
    location = models.ForeignKey(InventoryLocation, on_delete=models.PROTECT, related_name="sales")
    receipt_number = models.CharField(max_length=40)              # RCP{INITIALS}-DDMMYY-SEQ
    served_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    acted_as = models.CharField(max_length=15, blank=True)        # audit: which view the manager was in
    payment_method = models.CharField(max_length=15, choices=METHODS)
    customer_name = models.CharField(max_length=120, blank=True)  # every sale records who it went to
    customer_phone = models.CharField(max_length=30, blank=True)
    paid_into_momo = models.ForeignKey(MomoAccount, null=True, blank=True, on_delete=models.SET_NULL)
    paid_into_bank = models.ForeignKey(BankAccount, null=True, blank=True, on_delete=models.SET_NULL)
    debtor = models.ForeignKey(Debtor, null=True, blank=True, on_delete=models.SET_NULL, related_name="sales")
    discount_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    subtotal = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    amount_paid = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    balance = models.DecimalField(max_digits=16, decimal_places=2, default=0)


class SaleItem(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("production.Product", on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, default=0)  # COGS snapshot = production cost
    line_total = models.DecimalField(max_digits=16, decimal_places=2)


class DebtorPayment(TimeStamped):
    debtor = models.ForeignKey(Debtor, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    method = models.CharField(max_length=15)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    # snapshotted at the moment this payment was applied, so a receipt for an
    # old payment still shows the balance as it stood right then — not the
    # debtor's live balance, which keeps moving as later payments land
    balance_after = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)


class WholesaleOrder(TimeStamped):
    """Open order with deposits + deliveries; deposit is a liability until delivered."""
    STATUS = [("OPEN", "Open"), ("BILLED", "Billed"), ("COMPLETED", "Completed")]
    business = models.ForeignKey("platformadmin.Business", on_delete=models.CASCADE)
    location = models.ForeignKey(InventoryLocation, on_delete=models.PROTECT)
    customer_name = models.CharField(max_length=120)
    customer_phone = models.CharField(max_length=30, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="OPEN")


class Expense(TimeStamped):
    business = models.ForeignKey("platformadmin.Business", on_delete=models.CASCADE)
    location = models.ForeignKey(InventoryLocation, on_delete=models.PROTECT, related_name="expenses")
    category = models.CharField(max_length=60)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    note = models.CharField(max_length=200, blank=True)
    date = models.DateField()
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)


class StockRequest(TimeStamped):
    """Outlet/rep -> production. A request can be fulfilled in more than one
    go: whatever the factory holds ships now, and — if the requester chose to
    wait — the remainder stays on order (PARTIAL) until a new batch lands."""
    STATUS = [("DRAFT", "Draft"), ("SUBMITTED", "Submitted"), ("APPROVED", "Approved"),
              ("REJECTED", "Rejected"), ("PARTIAL", "Partially sent — remainder on order"),
              ("FULFILLED", "Fulfilled")]
    MODES = [("WAIT", "Send what's available now, keep the rest on order"),
             ("AVAILABLE_ONLY", "Send what's available now only, cancel the rest")]
    business = models.ForeignKey("platformadmin.Business", on_delete=models.CASCADE)
    requester_location = models.ForeignKey(InventoryLocation, on_delete=models.CASCADE)
    status = models.CharField(max_length=10, choices=STATUS, default="DRAFT")
    fulfillment_mode = models.CharField(max_length=15, choices=MODES, default="WAIT")
    lines = models.JSONField(default=list)           # [{product_id, quantity, fulfilled, dropped}]

    def line_progress(self):
        """Per line: requested, sent so far, still outstanding."""
        out = []
        for l in self.lines:
            qty = int(l.get("quantity", 0) or 0)
            sent = int(l.get("fulfilled", 0) or 0)
            out.append({"product_id": l.get("product_id"), "quantity": qty, "fulfilled": sent,
                        "dropped": int(l.get("dropped", 0) or 0), "remaining": max(qty - sent - int(l.get("dropped", 0) or 0), 0)})
        return out


class OutletTransfer(TimeStamped):
    """One outlet selling stock to another outlet within the system —
    cashier-initiated (unlike production.Distribution, which is Production
    -> outlet/rep). The sender's side is a normal Sale, posted immediately.
    The receiver's stock only lands, and their matching Expense only posts,
    once they confirm — same in-transit safety as Distribution."""
    STATUS = [("SENT", "Sent"), ("RECEIVED", "Received"), ("DISPUTED", "Disputed")]
    business = models.ForeignKey("platformadmin.Business", on_delete=models.CASCADE)
    from_location = models.ForeignKey(InventoryLocation, on_delete=models.PROTECT, related_name="transfers_sent")
    to_location = models.ForeignKey(InventoryLocation, on_delete=models.PROTECT, related_name="transfers_received")
    reference_number = models.CharField(max_length=40)          # TRF{INITIALS}-DDMMYY-SEQ
    date = models.DateField()
    status = models.CharField(max_length=10, choices=STATUS, default="SENT")
    sale = models.OneToOneField(Sale, null=True, blank=True, on_delete=models.SET_NULL, related_name="outlet_transfer")
    expense = models.OneToOneField(Expense, null=True, blank=True, on_delete=models.SET_NULL, related_name="outlet_transfer")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                                   related_name="outlet_transfers_created")
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="outlet_transfers_confirmed")


class OutletTransferLine(models.Model):
    transfer = models.ForeignKey(OutletTransfer, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("production.Product", on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()


class PendingAction(TimeStamped):
    """One request/approval engine for the whole system."""
    TYPES = [("SWAP", "Swap"), ("REFUND", "Refund"), ("STOCK_REQUEST", "Stock request")]
    STATUS = [("PENDING", "Pending"), ("APPROVED", "Approved"), ("REJECTED", "Rejected")]
    business = models.ForeignKey("platformadmin.Business", on_delete=models.CASCADE)
    action_type = models.CharField(max_length=15, choices=TYPES)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="requests")
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=10, choices=STATUS, default="PENDING")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="reviews")
    reject_reason = models.CharField(max_length=200, blank=True)
