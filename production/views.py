"""Catalog & Pricing — the manager's exclusive lock (owner can see/manage it
too, same precedent as Branches/Users). A product only distributes once
priced; see Product.save() for the DRAFT -> ACTIVE rule.

Everything below the pricing/distribution section is Production's own
module: raw materials in, formulas, batches, QA, out to distribution."""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import user_passes_test
from django.db.models import Count, ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from finance.models import Supplier, SupplierPayable
from sales.models import InventoryLocation, StockItem
from .models import (
    Category, Distribution, DistributionLine, FormulaLine, Product, ProductFormula,
    ProductionBatch, RawMaterial, RawMaterialPurchase,
)

catalog_manager_required = user_passes_test(lambda u: u.is_authenticated and u.role in ("OWNER", "MANAGER"))
catalog_staff_required = user_passes_test(
    lambda u: u.is_authenticated and u.role in ("OWNER", "MANAGER", "PRODUCTION"))
distribution_staff_required = user_passes_test(
    lambda u: u.is_authenticated and u.role in ("OWNER", "MANAGER", "PRODUCTION"))
production_staff_required = user_passes_test(lambda u: u.is_authenticated and u.role in ("OWNER", "PRODUCTION"))

TIER_LABELS = [("RETAIL", "retail_price", "Retail"), ("WHOLESALE", "wholesale_price", "Wholesale"),
               ("DISTRIBUTION", "distribution_price", "Distribution"), ("CUSTOM", "custom_price", "Custom")]


def _latest_cost(product):
    batch = ProductionBatch.objects.filter(product=product, status="COMPLETED").order_by("-date", "-id").first()
    return batch.unit_cost_at_production if batch else None


@catalog_staff_required
def product_list(request):
    biz = request.user.business
    products = list(Product.objects.filter(business=biz).select_related("category").order_by("status", "name"))
    for p in products:
        p.latest_cost = _latest_cost(p)
    return render(request, "production/products.html", {
        "products": products, "awaiting_count": sum(1 for p in products if p.status == "DRAFT"),
    })


@production_staff_required
def product_create(request):
    biz = request.user.business
    categories = Category.objects.filter(business=biz).order_by("name")
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        category = Category.objects.filter(pk=request.POST.get("category"), business=biz).first()
        if name and category:
            Product.objects.create(business=biz, category=category, name=name,
                                   pack_size=request.POST.get("pack_size", "").strip(),
                                   sku=request.POST.get("sku", "").strip())
        return redirect("production:product_list")
    return render(request, "production/product_create.html", {"categories": categories})


@catalog_manager_required
def product_price_edit(request, pk):
    biz = request.user.business
    product = get_object_or_404(Product, pk=pk, business=biz)

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
        product.save()
        return redirect("production:product_list")

    latest_cost = _latest_cost(product)
    margins = []
    for code, field, label in TIER_LABELS:
        price = getattr(product, field)
        if price is not None and latest_cost is not None:
            margins.append((label, price, price - latest_cost))
    return render(request, "production/product_price_edit.html", {
        "product": product, "latest_cost": latest_cost, "margins": margins,
    })


@distribution_staff_required
def distribution_create(request):
    """Production sends stock to a receiver (rep or outlet). Only ACTIVE
    products are offered — the pricing gate, enforced by construction."""
    biz = request.user.business
    locations = InventoryLocation.objects.filter(business=biz).exclude(type="PRODUCTION_STORE") \
        .select_related("branch", "rep")
    active_products = Product.objects.filter(business=biz, status="ACTIVE").order_by("name")
    factory_store = InventoryLocation.objects.filter(business=biz, type="PRODUCTION_STORE").first()

    if request.method == "POST":
        receiver = get_object_or_404(InventoryLocation, pk=request.POST.get("location"), business=biz)
        seq = Distribution.objects.filter(business=biz, date=date.today()).count() + 1
        dist = Distribution.objects.create(
            business=biz, receiver_location=receiver,
            delivery_note_number=f"DN-{request.user.username[:4].upper()}-{date.today():%d%m%y}-{seq:03d}",
            date=date.today(), status="SENT", created_by=request.user,
        )
        for pid, qty in zip(request.POST.getlist("product"), request.POST.getlist("quantity")):
            try:
                qty = int(qty)
            except (TypeError, ValueError):
                continue
            if not pid or qty <= 0:
                continue
            product = active_products.filter(pk=pid).first()
            if not product or not factory_store:
                continue
            store_item = StockItem.objects.filter(location=factory_store, product=product).first()
            if not store_item or store_item.quantity < qty:
                continue
            DistributionLine.objects.create(distribution=dist, product=product, quantity=qty)
            store_item.quantity -= qty
            store_item.save(update_fields=["quantity"])
        return redirect("production:distributions")

    return render(request, "production/distribution_create.html", {
        "locations": locations, "active_products": active_products,
    })


@distribution_staff_required
def distribution_list(request):
    biz = request.user.business
    distributions = Distribution.objects.filter(business=biz).select_related("receiver_location__branch") \
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
        for line in dist.lines.select_related("product"):
            item, _ = StockItem.objects.get_or_create(
                location=dist.receiver_location, product=line.product,
                defaults={"buying_price": 0, "selling_price": 0},
            )
            item.quantity += line.quantity
            item.save(update_fields=["quantity"])
        dist.status = "RECEIVED"
        dist.save(update_fields=["status"])
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
        uom = request.POST.get("unit_of_measure")
        try:
            reorder = Decimal(request.POST.get("reorder_level") or "0")
        except InvalidOperation:
            reorder = Decimal("0")
        if name and uom:
            RawMaterial.objects.create(business=biz, name=name, unit_of_measure=uom, reorder_level=reorder)
        return redirect("production:raw_materials")
    return render(request, "production/raw_material_create.html", {
        "uom_choices": RawMaterial._meta.get_field("unit_of_measure").choices,
    })


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
