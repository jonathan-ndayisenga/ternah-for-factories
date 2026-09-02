"""Catalog & Pricing — the manager's exclusive lock (owner can see/manage it
too, same precedent as Branches/Users). A product only distributes once
priced; see Product.save() for the DRAFT -> ACTIVE rule."""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from sales.models import InventoryLocation, StockItem
from .models import Distribution, DistributionLine, Product, ProductionBatch

catalog_manager_required = user_passes_test(lambda u: u.is_authenticated and u.role in ("OWNER", "MANAGER"))
distribution_staff_required = user_passes_test(
    lambda u: u.is_authenticated and u.role in ("OWNER", "MANAGER", "PRODUCTION"))

TIER_LABELS = [("RETAIL", "retail_price", "Retail"), ("WHOLESALE", "wholesale_price", "Wholesale"),
               ("DISTRIBUTION", "distribution_price", "Distribution"), ("CUSTOM", "custom_price", "Custom")]


def _latest_cost(product):
    batch = ProductionBatch.objects.filter(product=product, status="COMPLETED").order_by("-date", "-id").first()
    return batch.unit_cost_at_production if batch else None


@catalog_manager_required
def product_list(request):
    biz = request.user.business
    products = list(Product.objects.filter(business=biz).select_related("category").order_by("status", "name"))
    for p in products:
        p.latest_cost = _latest_cost(p)
    return render(request, "production/products.html", {
        "products": products, "awaiting_count": sum(1 for p in products if p.status == "DRAFT"),
    })


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
