from decimal import Decimal
from django.conf import settings
from django.db import models
from core.models import TimeStamped


# Suggestions only, offered via a <datalist> on the form — unit_of_measure
# itself is free text, so anything typed that isn't in this list is fine too.
UOM_SUGGESTIONS = [
    "mg", "g", "kg", "tonne", "ml", "L", "pcs", "dozen", "box", "bottle",
    "sachet", "roll", "bag", "tin", "drum", "carton", "pack",
]


class RawMaterial(TimeStamped):
    business = models.ForeignKey("platformadmin.Business", on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    # Free text, not a fixed choice list — a set of common units is offered
    # via the form's datalist, but the operator can type anything (e.g. "roll",
    # "sachet", "dozen") and it's saved as-is.
    unit_of_measure = models.CharField(max_length=20)
    reorder_level = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    is_active = models.BooleanField(default=True)   # soft delete — purchases/formula lines keep pointing at it

    def current_stock(self, branch=None):
        """The catalog entry is shared business-wide, but each factory holds
        its own physical stock — pass branch to see one factory's share;
        omit it for the business-wide total (used for cross-factory rollups)."""
        qs = self.purchases.all() if branch is None else self.purchases.filter(branch=branch)
        return qs.aggregate(s=models.Sum("remaining_quantity"))["s"] or Decimal("0")

    def latest_unit_cost(self, branch=None):
        qs = self.purchases.exclude(is_reversed=True)
        if branch is not None:
            qs = qs.filter(branch=branch)
        p = qs.order_by("-purchase_date").first()
        return p.unit_cost if p else Decimal("0")

    def __str__(self):
        return self.name


class RawMaterialPurchase(TimeStamped):
    """20 L at UGX 100,000 total -> unit_cost auto = 5,000/L. FEFO draw-down via remaining_quantity."""
    raw_material = models.ForeignKey(RawMaterial, on_delete=models.CASCADE, related_name="purchases")
    branch = models.ForeignKey("core.Branch", on_delete=models.PROTECT, related_name="raw_material_purchases",
                               )   # which factory holds this stock — the catalog entry is shared, this isn't
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    total_cost = models.DecimalField(max_digits=14, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, editable=False)
    remaining_quantity = models.DecimalField(max_digits=12, decimal_places=3)
    purchase_date = models.DateField()
    batch_number = models.CharField(max_length=60)
    expiry_date = models.DateField(null=True, blank=True)
    manufacture_date = models.DateField(null=True, blank=True)
    country_of_origin = models.CharField(max_length=80, blank=True)
    supplier = models.ForeignKey("finance.Supplier", null=True, blank=True, on_delete=models.SET_NULL)
    on_credit = models.BooleanField(default=False)   # True -> creates a SupplierPayable
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    is_reversed = models.BooleanField(default=False)   # a data-entry error, corrected by reversal + a fresh purchase

    def save(self, *args, **kwargs):
        self.unit_cost = (self.total_cost / self.quantity).quantize(Decimal("0.0001"))
        if self._state.adding and self.remaining_quantity is None:
            self.remaining_quantity = self.quantity
        super().save(*args, **kwargs)


class Category(TimeStamped):
    """Categories live in production so distribution stays consistent everywhere."""
    business = models.ForeignKey("platformadmin.Business", on_delete=models.CASCADE)
    name = models.CharField(max_length=80)

    class Meta:
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class Product(TimeStamped):
    """A product only distributes once it's priced — see save(). Buying/cost
    price is never stored here; it's stamped per-batch onto StockItem from
    ProductionBatch.unit_cost_at_production. Selling price is the opposite:
    set once here by the manager, read everywhere (no per-location prices)."""
    STATUS = [("DRAFT", "Draft"), ("ACTIVE", "Active"), ("ARCHIVED", "Archived")]
    business = models.ForeignKey("platformadmin.Business", on_delete=models.CASCADE)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    name = models.CharField(max_length=120)
    pack_size = models.CharField(max_length=40, blank=True)      # e.g. "30 g"
    sku = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="DRAFT")
    retail_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    wholesale_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    distribution_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    # Custom isn't a fixed price like the other three — the cashier types the
    # actual figure at sale time, bounded by this manager-set range (see
    # sales.views.record_sale). Both must be set for CUSTOM to be sellable at all.
    custom_price_min = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    custom_price_max = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    shelf_life_days = models.PositiveIntegerField(null=True, blank=True)   # optional -> auto-suggests batch expiry

    PRICE_TIERS = [("RETAIL", "retail_price"), ("WHOLESALE", "wholesale_price"),
                   ("DISTRIBUTION", "distribution_price")]   # CUSTOM is typed at sale time, not looked up here

    def save(self, *args, **kwargs):
        # retail is the mandatory baseline tier — set it and the product goes live
        # everywhere at once; the other three tiers are optional and don't gate this.
        if self.retail_price is not None and self.status == "DRAFT":
            self.status = "ACTIVE"
        super().save(*args, **kwargs)

    def price_for_tier(self, tier_code):
        return getattr(self, dict(self.PRICE_TIERS).get(tier_code, ""), None)

    def __str__(self):
        return self.name


class ProductFormula(TimeStamped):
    """Static once approved; edits create a new version. Batches pin their version."""
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="formulas")
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=10, choices=[("DRAFT", "Draft"), ("APPROVED", "Approved")], default="DRAFT")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    approved_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)   # deactivated formulas can't be picked for a new batch

    class Meta:
        unique_together = [("product", "version")]

    def unit_cost(self):
        """Cost of producing ONE unit = sum(line qty x raw material unit cost)."""
        return sum((l.quantity_per_unit * l.raw_material.latest_unit_cost() for l in self.lines.all()), Decimal("0"))


class FormulaLine(models.Model):
    formula = models.ForeignKey(ProductFormula, on_delete=models.CASCADE, related_name="lines")
    raw_material = models.ForeignKey(RawMaterial, on_delete=models.PROTECT)
    quantity_per_unit = models.DecimalField(max_digits=12, decimal_places=4)   # 0.5 L per 30 g tin


class ProductionBatch(TimeStamped):
    STATUS = [("PLANNED", "Planned"), ("DISPENSED", "Dispensed"), ("COMPLETED", "Completed")]
    business = models.ForeignKey("platformadmin.Business", on_delete=models.CASCADE)
    branch = models.ForeignKey("core.Branch", on_delete=models.PROTECT, related_name="production_batches",
                               )   # which factory ran this batch
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    formula = models.ForeignKey(ProductFormula, on_delete=models.PROTECT)      # version pinned
    batch_number = models.CharField(max_length=40)                              # BATCH{INITIALS}-DDMMYY-SEQ
    date = models.DateField()
    target_quantity = models.PositiveIntegerField()
    actual_quantity = models.PositiveIntegerField(null=True, blank=True)
    unit_cost_at_production = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    total_cost = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    remaining_quantity = models.PositiveIntegerField(default=0)   # this batch's own share of the factory store, FEFO
    status = models.CharField(max_length=10, choices=STATUS, default="PLANNED")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    manufacture_date = models.DateField(null=True, blank=True)   # set at Complete & QA, not at dispensing
    expiry_date = models.DateField(null=True, blank=True)        # auto-suggested from product.shelf_life_days

    def requirements(self):
        """Formula x target output -> total raw material needed per line."""
        return [(l.raw_material, l.quantity_per_unit * self.target_quantity) for l in self.formula.lines.all()]


class Dispensation(models.Model):
    """Traceability: which purchase batch fed which production batch (FEFO)."""
    batch = models.ForeignKey(ProductionBatch, on_delete=models.CASCADE, related_name="dispensations")
    raw_material = models.ForeignKey(RawMaterial, on_delete=models.PROTECT)
    purchase = models.ForeignKey(RawMaterialPurchase, on_delete=models.PROTECT)
    quantity_dispensed = models.DecimalField(max_digits=12, decimal_places=3)


class QAReport(TimeStamped):
    batch = models.OneToOneField(ProductionBatch, on_delete=models.CASCADE, related_name="qa")
    quality_notes = models.TextField()
    quantity_notes = models.TextField(blank=True)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)


class Distribution(TimeStamped):
    """Production -> a branch's inventory or a rep's inventory. Pending until receiver confirms."""
    STATUS = [("SENT", "Sent"), ("RECEIVED", "Received"), ("DISPUTED", "Disputed")]
    business = models.ForeignKey("platformadmin.Business", on_delete=models.CASCADE)
    sender_branch = models.ForeignKey("core.Branch", on_delete=models.PROTECT, related_name="distributions_sent",
                                      )   # which factory sent it — any factory can reach any outlet
    receiver_location = models.ForeignKey("sales.InventoryLocation", on_delete=models.PROTECT)
    delivery_note_number = models.CharField(max_length=40)                      # DN{INITIALS}-DDMMYY-SEQ
    date = models.DateField()
    status = models.CharField(max_length=10, choices=STATUS, default="SENT")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="distributions_confirmed")
    stock_request = models.ForeignKey("sales.StockRequest", null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="distributions")   # set when this delivery answers a request
    note = models.TextField(blank=True)   # e.g. "sent less than asked, rest still brewing"


class DistributionLine(models.Model):
    distribution = models.ForeignKey(Distribution, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    batch = models.ForeignKey(ProductionBatch, null=True, blank=True, on_delete=models.PROTECT,
                              related_name="distribution_lines")   # which batch this quantity came from (FEFO)
