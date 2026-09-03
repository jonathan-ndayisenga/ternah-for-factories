"""Catalog & Pricing — the manager's exclusive lock (owner can see/manage it
too, same precedent as Branches/Users). A product only distributes once
priced; see Product.save() for the DRAFT -> ACTIVE rule.

Everything below the pricing/distribution section is Production's own
module: raw materials in, formulas, batches, QA, out to distribution."""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.db.models import Count, F, ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from finance.models import Supplier, SupplierPayable
from finance.services import post_batch_completion, post_raw_material_purchase
from sales.models import InventoryLocation, StockItem, StockMovement, StockRequest
from .models import (
    Category, Dispensation, Distribution, DistributionLine, FormulaLine, Product, ProductFormula,
    ProductionBatch, QAReport, RawMaterial, RawMaterialPurchase, UOM_SUGGESTIONS,
)

catalog_staff_required = user_passes_test(
    lambda u: u.is_authenticated and u.role in ("OWNER", "MANAGER", "PRODUCTION"))
distribution_staff_required = user_passes_test(
    lambda u: u.is_authenticated and u.role in ("OWNER", "MANAGER", "PRODUCTION"))


def _has_production_access(u):
    """Production staff and Owner always have it; a Manager only does once
    the Owner has actually granted her the Production module — matches
    User.switchable_views(), which is what puts 'Production' in her nav
    switcher in the first place."""
    if not u.is_authenticated:
        return False
    if u.role in ("OWNER", "PRODUCTION"):
        return True
    return u.role == "MANAGER" and u.modules.filter(code="PRODUCTION").exists()


production_staff_required = user_passes_test(_has_production_access)

TIER_LABELS = [("RETAIL", "retail_price", "Retail"), ("WHOLESALE", "wholesale_price", "Wholesale"),
               ("DISTRIBUTION", "distribution_price", "Distribution")]


def _latest_cost(product):
    batch = ProductionBatch.objects.filter(product=product, status="COMPLETED").order_by("-date", "-id").first()
    return batch.unit_cost_at_production if batch else None


def _cost_stats(product):
    """Different batches of the same product can land at different unit
    costs (yield variance, raw material price drift) — surface the range,
    not just the latest figure, so a manager isn't pricing off a fluke batch."""
    costs = list(ProductionBatch.objects.filter(product=product, status="COMPLETED")
                 .exclude(unit_cost_at_production__isnull=True)
                 .order_by("-date", "-id").values_list("unit_cost_at_production", flat=True))
    if not costs:
        return None
    return {"latest": costs[0], "min": min(costs), "max": max(costs), "count": len(costs)}


@catalog_staff_required
def product_list(request):
    biz = request.user.business
    products = list(Product.objects.filter(business=biz).select_related("category").order_by("status", "name"))
    for p in products:
        p.cost_stats = _cost_stats(p)
    return render(request, "production/products.html", {
        "products": products, "awaiting_count": sum(1 for p in products if p.status == "DRAFT"),
    })


@catalog_staff_required
def product_cost_history(request, pk):
    biz = request.user.business
    product = get_object_or_404(Product, pk=pk, business=biz)
    batches = (ProductionBatch.objects.filter(product=product, status="COMPLETED")
               .select_related("formula").order_by("-date", "-id"))
    stats = _cost_stats(product)
    return render(request, "production/product_cost_history.html", {
        "product": product, "batches": batches, "stats": stats,
    })


@production_staff_required
def product_create(request):
    biz = request.user.business
    categories = Category.objects.filter(business=biz).order_by("name")
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        category = Category.objects.filter(pk=request.POST.get("category"), business=biz).first()
        if name and category:
            try:
                shelf_life_days = int(request.POST.get("shelf_life_days") or 0) or None
            except ValueError:
                shelf_life_days = None
            Product.objects.create(business=biz, category=category, name=name,
                                   pack_size=request.POST.get("pack_size", "").strip(),
                                   sku=request.POST.get("sku", "").strip(),
                                   shelf_life_days=shelf_life_days)
        return redirect("production:product_list")
    return render(request, "production/product_create.html", {"categories": categories})


@catalog_staff_required
def product_price_edit(request, pk):
    biz = request.user.business
    product = get_object_or_404(Product, pk=pk, business=biz)
    latest_cost = _latest_cost(product)
    error = None

    if request.method == "POST":
        for code, field, label in TIER_LABELS:
            raw = request.POST.get(field, "").strip()
            if not raw:
                setattr(product, field, None)
                continue
            try:
                setattr(product, field, Decimal(raw))
            except InvalidOperation:
                pass

        cmin_raw = request.POST.get("custom_price_min", "").strip()
        cmax_raw = request.POST.get("custom_price_max", "").strip()
        try:
            cmin = Decimal(cmin_raw) if cmin_raw else None
            cmax = Decimal(cmax_raw) if cmax_raw else None
        except InvalidOperation:
            cmin = cmax = None
        product.custom_price_min, product.custom_price_max = cmin, cmax
        if cmin is not None and latest_cost is not None and cmin < latest_cost:
            error = f"Custom price minimum can't be below cost (UGX {latest_cost:.2f}) — nothing saved."
        elif cmin is not None and cmax is not None and cmin > cmax:
            error = "Custom price minimum can't be higher than the maximum — nothing saved."
        else:
            product.save()
            return redirect("production:product_list")

    margins = []
    for code, field, label in TIER_LABELS:
        price = getattr(product, field)
        if price is not None and latest_cost is not None:
            margins.append((label, price, price - latest_cost))
    custom_margin_min = None
    if product.custom_price_min is not None and latest_cost is not None:
        custom_margin_min = product.custom_price_min - latest_cost
    return render(request, "production/product_price_edit.html", {
        "product": product, "latest_cost": latest_cost, "margins": margins, "error": error,
        "custom_margin_min": custom_margin_min,
    })


def _batches_available(product):
    """COMPLETED batches of this product still holding stock at the factory,
    earliest expiry first (nulls last), then oldest manufactured — the same
    FEFO order raw materials already dispense in."""
    return ProductionBatch.objects.filter(product=product, status="COMPLETED", remaining_quantity__gt=0) \
        .order_by(F("expiry_date").asc(nulls_last=True), "manufacture_date", "date", "id")


def _dispense_finished_goods_fefo(dist, product, qty):
    """Walks this product's batches FEFO, writing one DistributionLine per
    batch actually touched (so 'which batch went out' is a real record, not
    a guess) and decrementing each batch's own remaining_quantity."""
    remaining = qty
    for batch in _batches_available(product):
        if remaining <= 0:
            break
        take = min(remaining, batch.remaining_quantity)
        DistributionLine.objects.create(distribution=dist, product=product, quantity=take, batch=batch)
        batch.remaining_quantity -= take
        batch.save(update_fields=["remaining_quantity"])
        remaining -= take
    if remaining > 0:
        # more was requested than any batch could account for (e.g. stale
        # per-batch figures) — still honour the sale against the aggregate
        # stock, just without a batch attached to this last slice
        DistributionLine.objects.create(distribution=dist, product=product, quantity=remaining, batch=None)


@distribution_staff_required
def distribution_create(request):
    """Production sends stock to a receiver (rep or outlet). Only ACTIVE
    products are offered — the pricing gate, enforced by construction.
    Can never send more than the finished-goods store actually holds —
    validated against the same figures shown live in the form."""
    biz = request.user.business
    locations = InventoryLocation.objects.filter(business=biz).exclude(type="PRODUCTION_STORE") \
        .select_related("branch", "rep")
    active_products = Product.objects.filter(business=biz, status="ACTIVE").order_by("name")
    factory_store = InventoryLocation.objects.filter(business=biz, type="PRODUCTION_STORE").first()

    stock_by_product = {
        item.product_id: item.quantity
        for item in StockItem.objects.filter(location=factory_store, product__in=active_products)
    } if factory_store else {}
    batch_data = {}
    for p in active_products:
        p.available_stock = stock_by_product.get(p.id, 0)
        batch_data[str(p.id)] = [
            {"batch_number": b.batch_number, "manufacture_date": b.manufacture_date.strftime("%d %b %Y") if b.manufacture_date else "—",
             "expiry_date": b.expiry_date.strftime("%d %b %Y") if b.expiry_date else None, "remaining": b.remaining_quantity}
            for b in _batches_available(p)
        ]

    error = None
    if request.method == "POST":
        receiver = get_object_or_404(InventoryLocation, pk=request.POST.get("location"), business=biz)
        lines = []
        requested = {}   # product_id -> total qty requested across rows, so duplicate rows are caught too
        for pid, qty in zip(request.POST.getlist("product"), request.POST.getlist("quantity")):
            try:
                qty = int(qty)
            except (TypeError, ValueError):
                continue
            if not pid or qty <= 0:
                continue
            product = active_products.filter(pk=pid).first()
            if not product:
                continue
            requested[product.id] = requested.get(product.id, 0) + qty
            lines.append((product, qty))

        if not factory_store or not lines:
            error = "Add at least one product to send."
        else:
            for pid, total_qty in requested.items():
                available = stock_by_product.get(pid, 0)
                if total_qty > available:
                    product = active_products.get(pk=pid)
                    error = (f"Can't send {total_qty} of {product.name} — only {available} available "
                             f"in the finished-goods store.")
                    break

        if not error:
            seq = Distribution.objects.filter(business=biz, date=date.today()).count() + 1
            dist = Distribution.objects.create(
                business=biz, receiver_location=receiver,
                delivery_note_number=f"DN-{request.user.username[:4].upper()}-{date.today():%d%m%y}-{seq:03d}",
                date=date.today(), status="SENT", created_by=request.user,
            )
            for product, qty in lines:
                store_item = StockItem.objects.filter(location=factory_store, product=product).first()
                _dispense_finished_goods_fefo(dist, product, qty)
                store_item.quantity -= qty
                store_item.save(update_fields=["quantity"])
                StockMovement.objects.create(location=factory_store, product=product, quantity=-qty,
                                             reason="DISTRIBUTION", reference=dist.delivery_note_number,
                                             moved_by=request.user, counterparty=receiver)
            return redirect("production:distributions")

    return render(request, "production/distribution_create.html", {
        "locations": locations, "active_products": active_products, "error": error, "batch_data": batch_data,
    })


@distribution_staff_required
def distribution_list(request):
    biz = request.user.business
    distributions = Distribution.objects.filter(business=biz) \
        .select_related("receiver_location__branch", "receiver_location__rep", "created_by", "confirmed_by") \
        .prefetch_related("lines__product").order_by("-date", "-id")
    return render(request, "production/distributions.html", {"distributions": distributions})


@distribution_staff_required
def distribution_confirm(request, pk):
    """Receiver's side of the loop: stock only actually lands in their
    inventory once they confirm — a dispute leaves it in limbo, not silently
    landed or silently lost. Reached from the manager's Inventory page (their
    normal home for this) or Production's own Distributions list."""
    next_url = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        next_url = "production:distributions"

    biz = request.user.business
    dist = get_object_or_404(Distribution, pk=pk, business=biz, status="SENT")
    action = request.POST.get("action")
    if action == "confirm":
        factory_store = InventoryLocation.objects.filter(business=biz, type="PRODUCTION_STORE").first()
        for line in dist.lines.select_related("product"):
            # carry the factory's current cost basis (set at batch
            # completion) over — becomes the receiver's buying price for
            # this product, same everywhere stock lands
            source_cost = StockItem.objects.filter(
                location=factory_store, product=line.product).values_list("buying_price", flat=True).first() or 0
            item, _ = StockItem.objects.get_or_create(
                location=dist.receiver_location, product=line.product,
                defaults={"buying_price": source_cost, "selling_price": 0},
            )
            item.quantity += line.quantity
            item.buying_price = source_cost
            item.save(update_fields=["quantity", "buying_price"])
            StockMovement.objects.create(location=dist.receiver_location, product=line.product, quantity=line.quantity,
                                         reason="DISTRIBUTION", reference=dist.delivery_note_number,
                                         moved_by=request.user, counterparty=factory_store)
        dist.status = "RECEIVED"
        dist.confirmed_by = request.user
        dist.save(update_fields=["status", "confirmed_by"])
    elif action == "dispute":
        dist.status = "DISPUTED"
        dist.save(update_fields=["status"])
    return redirect(next_url)


# ---------------------------------------------------------------- Categories

@production_staff_required
def category_list(request):
    biz = request.user.business
    error = None
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create":
            name = request.POST.get("name", "").strip()
            if name:
                Category.objects.create(business=biz, name=name)
            return redirect("production:categories")
        elif action == "rename":
            cat = get_object_or_404(Category, pk=request.POST.get("category_id"), business=biz)
            new_name = request.POST.get("new_name", "").strip()
            if new_name:
                cat.name = new_name
                cat.save(update_fields=["name"])
            return redirect("production:categories")
        elif action == "delete":
            cat = get_object_or_404(Category, pk=request.POST.get("category_id"), business=biz)
            try:
                cat.delete()
                return redirect("production:categories")
            except ProtectedError:
                error = f'Can\'t delete "{cat.name}" — it still has products under it.'

    categories = Category.objects.filter(business=biz).annotate(product_count=Count("products")).order_by("name")
    return render(request, "production/categories.html", {"categories": categories, "error": error})


# ------------------------------------------------------------ Raw Materials

@production_staff_required
def raw_material_list(request):
    biz = request.user.business
    materials = list(RawMaterial.objects.filter(business=biz).order_by("name"))
    for m in materials:
        m.stock = m.current_stock()
        m.cost = m.latest_unit_cost()
        m.low = m.stock <= m.reorder_level
    return render(request, "production/raw_materials.html", {"materials": materials})


@production_staff_required
def raw_material_create(request):
    biz = request.user.business
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        uom = request.POST.get("unit_of_measure", "").strip()
        try:
            reorder = Decimal(request.POST.get("reorder_level") or "0")
        except InvalidOperation:
            reorder = Decimal("0")
        if name and uom:
            RawMaterial.objects.create(business=biz, name=name, unit_of_measure=uom, reorder_level=reorder)
        return redirect("production:raw_materials")
    return render(request, "production/raw_material_create.html", {"uom_suggestions": UOM_SUGGESTIONS})


@production_staff_required
def raw_material_purchase(request, pk):
    biz = request.user.business
    material = get_object_or_404(RawMaterial, pk=pk, business=biz)

    if request.method == "POST":
        try:
            quantity = Decimal(request.POST.get("quantity", ""))
            total_cost = Decimal(request.POST.get("total_cost", ""))
        except InvalidOperation:
            return redirect("production:raw_material_purchase", pk=material.pk)
        if quantity <= 0 or total_cost <= 0:
            return redirect("production:raw_material_purchase", pk=material.pk)

        supplier = None
        supplier_name = request.POST.get("supplier", "").strip()
        if supplier_name:
            supplier, _ = Supplier.objects.get_or_create(business=biz, name=supplier_name)
        on_credit = "on_credit" in request.POST

        purchase = RawMaterialPurchase.objects.create(
            raw_material=material, quantity=quantity, total_cost=total_cost,
            purchase_date=request.POST.get("purchase_date") or date.today(),
            batch_number=request.POST.get("batch_number", "").strip(),
            expiry_date=request.POST.get("expiry_date") or None,
            manufacture_date=request.POST.get("manufacture_date") or None,
            country_of_origin=request.POST.get("country_of_origin", "").strip(),
            supplier=supplier, on_credit=on_credit,
        )
        if on_credit and supplier:
            SupplierPayable.objects.create(
                supplier=supplier,
                description=f"{material.name} — {purchase.batch_number or purchase.purchase_date}",
                total_amount=total_cost, status="OPEN",
            )
        post_raw_material_purchase(purchase)
        return redirect("production:raw_materials")

    return render(request, "production/raw_material_purchase.html", {"material": material})


@production_staff_required
def raw_material_movements(request, pk):
    material = get_object_or_404(RawMaterial, pk=pk, business=request.user.business)
    entries = []
    for p in material.purchases.all():
        entries.append({"date": p.purchase_date, "type": "Purchase", "change": p.quantity,
                        "ref": p.batch_number or "—"})
    for d in material.dispensation_set.select_related("batch"):
        entries.append({"date": d.batch.date, "type": "Dispensed", "change": -d.quantity_dispensed,
                        "ref": d.batch.batch_number})
    entries.sort(key=lambda e: e["date"], reverse=True)
    return render(request, "production/raw_material_movements.html", {"material": material, "entries": entries})


# --------------------------------------------------------------- Formulas

@production_staff_required
def formula_edit(request, product_pk):
    """The static BOM per product. Approving locks it; editing an approved
    formula never mutates it — it creates version N+1 as a new draft, so
    ProductionBatch.formula (already pinned per batch) keeps old batches
    honest about which recipe actually made them."""
    biz = request.user.business
    product = get_object_or_404(Product, pk=product_pk, business=biz)
    materials = RawMaterial.objects.filter(business=biz).order_by("name")
    latest = product.formulas.order_by("-version").first()

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "new_version" and latest and latest.status == "APPROVED":
            new_formula = ProductFormula.objects.create(product=product, version=latest.version + 1, status="DRAFT")
            for line in latest.lines.all():
                FormulaLine.objects.create(formula=new_formula, raw_material=line.raw_material,
                                           quantity_per_unit=line.quantity_per_unit)
            return redirect("production:formula_edit", product_pk=product.pk)

        if action in ("deactivate", "reactivate") and latest and latest.status == "APPROVED":
            latest.is_active = (action == "reactivate")
            latest.save(update_fields=["is_active"])
            return redirect("production:formula_edit", product_pk=product.pk)

        if action in ("save", "approve"):
            formula = latest if latest and latest.status == "DRAFT" else None
            if formula is None:
                formula = ProductFormula.objects.create(
                    product=product, version=(latest.version + 1) if latest else 1, status="DRAFT")
            formula.lines.all().delete()
            for mid, qty in zip(request.POST.getlist("raw_material"), request.POST.getlist("quantity_per_unit")):
                try:
                    qty = Decimal(qty)
                except InvalidOperation:
                    continue
                if not mid or qty <= 0:
                    continue
                material = materials.filter(pk=mid).first()
                if material:
                    FormulaLine.objects.create(formula=formula, raw_material=material, quantity_per_unit=qty)
            if action == "approve" and formula.lines.exists():
                formula.status = "APPROVED"
                formula.approved_by = request.user
                formula.approved_at = timezone.now()
                formula.save(update_fields=["status", "approved_by", "approved_at"])
            return redirect("production:formula_edit", product_pk=product.pk)

    current = product.formulas.order_by("-version").first()
    lines = current.lines.select_related("raw_material").all() if current else []
    unit_cost = current.unit_cost() if current else Decimal("0")
    versions = product.formulas.order_by("-version")
    return render(request, "production/formula_edit.html", {
        "product": product, "current": current, "lines": lines, "unit_cost": unit_cost,
        "materials": materials, "versions": versions,
    })


# ------------------------------------------------------- Batches & Dispensing

@production_staff_required
def batch_create(request):
    """Target quantity x approved formula -> live requirements/shortfall
    (client-side, using the same embedded-data technique as the POS). On
    submit: verify stock covers every line first (all-or-nothing — no
    partial batches), then dispense FEFO — earliest expiry first, falling
    back to purchase date — recording exactly which purchase fed the batch."""
    biz = request.user.business
    products, batch_data = [], {}
    for p in Product.objects.filter(business=biz).select_related("category"):
        # the most recent APPROVED version is the one that counts — if it's
        # been deactivated, don't silently fall back to an older approved
        # version, since that could dispense against a stale recipe/costing
        formula = p.formulas.filter(status="APPROVED").order_by("-version").first()
        if not formula or not formula.is_active:
            continue
        products.append(p)
        batch_data[str(p.pk)] = {
            "unit_cost": float(formula.unit_cost()),
            "lines": [
                {"material": l.raw_material.name, "uom": l.raw_material.unit_of_measure,
                 "qty_per_unit": float(l.quantity_per_unit), "stock": float(l.raw_material.current_stock())}
                for l in formula.lines.select_related("raw_material").all()
            ],
        }

    if request.method == "POST":
        product = get_object_or_404(Product, pk=request.POST.get("product"), business=biz)
        formula = product.formulas.filter(status="APPROVED").order_by("-version").first()
        try:
            target = int(request.POST.get("target_quantity"))
        except (TypeError, ValueError):
            target = 0
        if not formula or not formula.is_active or target <= 0:
            return redirect("production:batch_create")

        lines = list(formula.lines.select_related("raw_material").all())
        for line in lines:
            needed = line.quantity_per_unit * target
            if line.raw_material.current_stock() < needed:
                return redirect("production:batch_create")   # shortfall — the live table should have caught this

        today = date.today()
        seq = ProductionBatch.objects.filter(business=biz, date=today).count() + 1
        batch = ProductionBatch.objects.create(
            business=biz, product=product, formula=formula,
            batch_number=f"BATCH{request.user.username[:2].upper()}-{today:%d%m%y}-{seq:02d}",
            date=today, target_quantity=target, status="DISPENSED", created_by=request.user,
        )
        for line in lines:
            needed = line.quantity_per_unit * target
            purchases = RawMaterialPurchase.objects.filter(
                raw_material=line.raw_material, remaining_quantity__gt=0
            ).order_by(F("expiry_date").asc(nulls_last=True), "purchase_date")
            for purchase in purchases:
                if needed <= 0:
                    break
                take = min(needed, purchase.remaining_quantity)
                Dispensation.objects.create(batch=batch, raw_material=line.raw_material,
                                            purchase=purchase, quantity_dispensed=take)
                purchase.remaining_quantity -= take
                purchase.save(update_fields=["remaining_quantity"])
                needed -= take
        # dispensing just starts the physical run — actual output and QA only
        # exist once it's really done, so land on the queue, not the complete
        # form for this one batch
        return redirect("production:processing")

    return render(request, "production/batch_create.html", {
        "products": products, "batch_data": batch_data,
        "preselect_product": request.GET.get("product", ""),   # e.g. arriving from a short stock request
        "preselect_quantity": request.GET.get("quantity", ""),
    })


@production_staff_required
def batch_complete(request, pk):
    """Actual output (variance vs target), QA notes, and this is where the
    unit cost becomes final: total dispensed cost / actual output, not
    target — then that cost lands with the stock in the finished-goods store."""
    biz = request.user.business
    batch = get_object_or_404(ProductionBatch, pk=pk, business=biz, status="DISPENSED")

    if request.method == "POST":
        try:
            actual = int(request.POST.get("actual_quantity"))
        except (TypeError, ValueError):
            actual = 0
        if actual <= 0:
            return redirect("production:batch_complete", pk=batch.pk)

        manufacture_date = request.POST.get("manufacture_date") or date.today()
        expiry_date = request.POST.get("expiry_date") or None
        if isinstance(manufacture_date, str):
            manufacture_date = date.fromisoformat(manufacture_date)
        if expiry_date:
            expiry_date = date.fromisoformat(expiry_date)
            if expiry_date <= manufacture_date:
                expiry_date = None   # nonsensical — drop it rather than stamp bad data

        total_cost = sum(
            (d.quantity_dispensed * d.purchase.unit_cost for d in batch.dispensations.select_related("purchase")),
            Decimal("0"),
        )
        batch.actual_quantity = actual
        batch.unit_cost_at_production = (total_cost / actual).quantize(Decimal("0.0001"))
        batch.total_cost = total_cost.quantize(Decimal("0.01"))
        batch.remaining_quantity = actual   # this batch's own share of the factory store, undistributed so far
        batch.status = "COMPLETED"
        batch.manufacture_date = manufacture_date
        batch.expiry_date = expiry_date
        batch.save(update_fields=["actual_quantity", "unit_cost_at_production", "total_cost", "remaining_quantity",
                                  "status", "manufacture_date", "expiry_date"])

        QAReport.objects.create(
            batch=batch, quality_notes=request.POST.get("quality_notes", "").strip(),
            quantity_notes=request.POST.get("quantity_notes", "").strip(), author=request.user,
        )

        factory_store = InventoryLocation.objects.filter(business=biz, type="PRODUCTION_STORE").first()
        if factory_store:
            item, _ = StockItem.objects.get_or_create(
                location=factory_store, product=batch.product,
                defaults={"buying_price": batch.unit_cost_at_production},
            )
            item.quantity += actual
            item.buying_price = batch.unit_cost_at_production
            item.save(update_fields=["quantity", "buying_price"])
            StockMovement.objects.create(location=factory_store, product=batch.product, quantity=actual,
                                         reason="DISTRIBUTION", reference=batch.batch_number, moved_by=request.user)
        post_batch_completion(batch)
        return redirect("reports:production_batch_detail", pk=batch.pk)

    return render(request, "production/batch_complete.html", {"batch": batch})


# ------------------------------------------------------------ Stock Requests

def _request_plan(stock_request, factory_store):
    """For each line of a request: what was asked for, what's already gone
    out, what's still owed, what the factory holds right now, what would ship
    if fulfilled this minute, the shortfall, and the FEFO batches that
    shipment would draw on — the whole picture before anyone clicks Fulfil."""
    plan, any_shortfall, any_sendable = [], False, False
    for l in stock_request.line_progress():
        product = Product.objects.filter(pk=l["product_id"]).first()
        remaining = l["remaining"]
        item = StockItem.objects.filter(location=factory_store, product=product).first() \
            if (product and factory_store) else None
        available = item.quantity if item else 0
        send_now = min(remaining, available)
        short = remaining - send_now
        batches, left = [], send_now
        if product:
            for b in _batches_available(product):
                if left <= 0:
                    break
                take = min(left, b.remaining_quantity)
                batches.append({"batch": b, "take": take})
                left -= take
        plan.append({**l, "product": product, "available": available, "send_now": send_now,
                     "short": short, "batches": batches})
        any_shortfall = any_shortfall or short > 0
        any_sendable = any_sendable or send_now > 0
    return plan, any_shortfall, any_sendable


@production_staff_required
def stock_request_list(request):
    biz = request.user.business
    factory_store = InventoryLocation.objects.filter(business=biz, type="PRODUCTION_STORE").first()
    requests = StockRequest.objects.filter(business=biz, status__in=("SUBMITTED", "PARTIAL")) \
        .select_related("requester_location__branch", "requester_location__rep") \
        .prefetch_related("distributions__lines__product", "distributions__lines__batch").order_by("created_at")
    for r in requests:
        r.plan, r.any_shortfall, r.any_sendable = _request_plan(r, factory_store)
    return render(request, "production/stock_requests.html", {"requests": requests})


@production_staff_required
def stock_request_fulfill(request, pk):
    """Fulfilling one becomes a distribution — same Active-only gate, same
    stock decrement — just sourced from the request's lines instead of a
    hand-picked cart."""
    biz = request.user.business
    stock_request = get_object_or_404(StockRequest, pk=pk, business=biz, status__in=("SUBMITTED", "PARTIAL"))
    if request.method != "POST":
        return redirect("production:stock_requests")

    factory_store = InventoryLocation.objects.filter(business=biz, type="PRODUCTION_STORE").first()
    plan, any_shortfall, any_sendable = _request_plan(stock_request, factory_store)
    if not factory_store or not any_sendable:
        messages.error(request, "Nothing on this request is in the factory store right now — "
                                "start a new batch first, then come back to it.")
        return redirect("production:stock_requests")

    today = date.today()
    seq = Distribution.objects.filter(business=biz, date=today).count() + 1
    dist = Distribution.objects.create(
        business=biz, receiver_location=stock_request.requester_location,
        delivery_note_number=f"DN-{request.user.username[:4].upper()}-{today:%d%m%y}-{seq:03d}",
        date=today, status="SENT", created_by=request.user, stock_request=stock_request,
        note=request.POST.get("note", "").strip(),
    )
    new_lines, sent_bits, short_bits = [], [], []
    for entry in plan:
        product, auto_send, short = entry["product"], entry["send_now"], entry["short"]
        line = {"product_id": entry["product_id"], "quantity": entry["quantity"],
                "fulfilled": entry["fulfilled"], "dropped": entry["dropped"]}

        # a manager can send less than the full amount on offer (holding
        # some back for another outlet, pacing deliveries, etc.) — but never
        # more than what's actually owed and in stock, so the field only
        # ever clamps down from auto_send, never up
        qty_raw = request.POST.get(f"qty_{entry['product_id']}")
        if qty_raw not in (None, ""):
            try:
                send_now = max(0, min(int(qty_raw), auto_send))
            except (TypeError, ValueError):
                send_now = auto_send
        else:
            send_now = auto_send

        if product and product.status == "ACTIVE" and send_now > 0:
            store_item = StockItem.objects.filter(location=factory_store, product=product).first()
            _dispense_finished_goods_fefo(dist, product, send_now)
            store_item.quantity -= send_now
            store_item.save(update_fields=["quantity"])
            StockMovement.objects.create(location=factory_store, product=product, quantity=-send_now,
                                         reason="DISTRIBUTION", reference=dist.delivery_note_number,
                                         moved_by=request.user, counterparty=stock_request.requester_location)
            line["fulfilled"] += send_now
            sent_bits.append(f"{product.name} ×{send_now}")

        if short > 0 and stock_request.fulfillment_mode == "AVAILABLE_ONLY":
            # a genuine stock shortfall, and they said don't wait for it —
            # but stock a manager deliberately held back (not a shortfall)
            # always stays queued, regardless of that preference
            line["dropped"] += short

        outstanding = line["quantity"] - line["fulfilled"] - line["dropped"]
        if outstanding > 0:
            short_bits.append(f"{product.name if product else 'unknown product'} ×{outstanding}")
        new_lines.append(line)

    stock_request.lines = new_lines
    still_owed = any(l["quantity"] - l["fulfilled"] - l["dropped"] > 0 for l in new_lines)
    stock_request.status = "PARTIAL" if still_owed else "FULFILLED"
    stock_request.save(update_fields=["lines", "status"])

    msg = f"{dist.delivery_note_number} sent: {', '.join(sent_bits)}."
    if short_bits:
        msg += f" Still on order: {', '.join(short_bits)} — start a new batch to cover it."
    messages.success(request, msg)
    return redirect("production:stock_requests")


# --------------------------------------------------------------- Dashboard

@production_staff_required
def dashboard(request):
    biz = request.user.business
    today = date.today()
    materials = list(RawMaterial.objects.filter(business=biz))
    for m in materials:
        m.stock = m.current_stock()
    low_stock = [m for m in materials if m.stock <= m.reorder_level]
    expiring = RawMaterialPurchase.objects.filter(
        raw_material__business=biz, remaining_quantity__gt=0,
        expiry_date__isnull=False, expiry_date__lte=today + timedelta(days=60),
    ).select_related("raw_material").order_by("expiry_date")[:10]

    factory_store = InventoryLocation.objects.filter(business=biz, type="PRODUCTION_STORE").first()
    store_items = StockItem.objects.filter(location=factory_store).select_related("product") \
        if factory_store else StockItem.objects.none()
    finished_expiring = ProductionBatch.objects.filter(
        business=biz, status="COMPLETED", expiry_date__isnull=False, expiry_date__lte=today + timedelta(days=60),
    ).select_related("product").order_by("expiry_date")[:10]

    return render(request, "production/dashboard.html", {
        "low_stock": low_stock,
        "expiring": expiring,
        "finished_expiring": finished_expiring,
        "store_items": store_items,
        "recent_batches": ProductionBatch.objects.filter(business=biz).order_by("-date", "-id")[:8],
        "processing_count": ProductionBatch.objects.filter(business=biz, status="DISPENSED").count(),
        "awaiting_confirmation": Distribution.objects.filter(business=biz, status="SENT").count(),
        "stock_requests_count": StockRequest.objects.filter(business=biz, status__in=("SUBMITTED", "PARTIAL")).count(),
        "awaiting_pricing": Product.objects.filter(business=biz, status="DRAFT").count(),
    })


@production_staff_required
def processing_list(request):
    """The queue between dispensing and the finished-goods store: batches
    that have had raw materials drawn but haven't been completed with
    actual output + QA yet."""
    batches = ProductionBatch.objects.filter(business=request.user.business, status="DISPENSED") \
        .select_related("product").order_by("date")
    return render(request, "production/processing.html", {"batches": batches})
