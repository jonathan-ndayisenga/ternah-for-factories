"""
Owner graphs & stats — Ternah for Factories.
Every endpoint returns JSON for Chart.js; the dashboard shell loads
each card lazily with HTMX. Everything is branch-aware so the owner's
per-branch view 'makes sense': users -> branch, rep inventories -> branch,
sales/expenses/debtors -> location -> branch.
"""
from datetime import timedelta
from decimal import Decimal
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from production.models import Dispensation, DistributionLine, Product, ProductionBatch, RawMaterial, RawMaterialPurchase
from sales.models import Debtor, Expense, InventoryLocation, Sale, SaleItem, StockItem

owner_required = user_passes_test(lambda u: u.is_authenticated and u.role == "OWNER")


def _biz(request):
    return request.user.business


def _branch_filter(request, qs, path="location__branch"):
    """Managers are pinned to their own branch regardless of what's in the
    query string; owners can filter by any branch (or none, for all of them)."""
    if request.user.role == "MANAGER":
        return qs.filter(**{f"{path}_id": request.user.branch_id})
    branch_id = request.GET.get("branch")
    return qs.filter(**{f"{path}_id": branch_id}) if branch_id else qs


@login_required
def owner_dashboard(request):
    biz = _biz(request)
    print_title = "Owner Dashboard" if request.user.role == "OWNER" else f"{request.user.branch.name} Dashboard"
    return render(request, "reports/owner_dashboard.html", {"branches": biz.branches.all(), "print_title": print_title})


def _selected_branch(request):
    """The branch this dashboard view is currently pinned to, if any — the
    manager's own, or whatever the owner picked in the filter. None means
    'all branches' for an owner."""
    if request.user.role == "MANAGER":
        return request.user.branch
    branch_id = request.GET.get("branch")
    return request.user.business.branches.filter(pk=branch_id).first() if branch_id else None


@login_required
def stats_cards(request):
    """Headline numbers. A factory branch doesn't sell — filtering to it swaps
    these for stock-level numbers instead of pretending it has sales figures."""
    branch = _selected_branch(request)
    if branch and branch.kind == "FACTORY":
        return factory_stats_cards(request, branch)

    biz, today = _biz(request), timezone.localdate()
    sales = _branch_filter(request, Sale.objects.filter(business=biz))
    month = sales.filter(created_at__date__gte=today.replace(day=1))
    gp = SaleItem.objects.filter(sale__in=month).aggregate(
        p=Sum(ExpressionWrapper(F("line_total") - F("quantity") * F("unit_cost"),
                                output_field=DecimalField())))["p"] or 0
    return render(request, "partials/stats_cards.html", {
        "today_sales": sales.filter(created_at__date=today).aggregate(s=Sum("total"))["s"] or 0,
        "mtd_sales": month.aggregate(s=Sum("total"))["s"] or 0,
        "mtd_gross_profit": gp,
        "outstanding_debt": sales.aggregate(s=Sum("balance"))["s"] or 0,
    })


def factory_stats_cards(request, branch):
    biz, today = _biz(request), timezone.localdate()
    materials = list(RawMaterial.objects.filter(business=biz))
    low_stock = sum(1 for m in materials if m.current_stock(branch=branch) <= m.reorder_level)
    expiring = RawMaterialPurchase.objects.filter(
        raw_material__business=biz, branch=branch, remaining_quantity__gt=0,
        expiry_date__isnull=False, expiry_date__lte=today + timedelta(days=60),
    ).count()
    factory_store = InventoryLocation.objects.filter(business=biz, branch=branch, type="PRODUCTION_STORE").first()
    store_value = 0
    if factory_store:
        store_value = StockItem.objects.filter(location=factory_store).aggregate(
            v=Sum(ExpressionWrapper(F("quantity") * F("buying_price"), output_field=DecimalField(max_digits=16, decimal_places=2)))
        )["v"] or 0
    processing = ProductionBatch.objects.filter(business=biz, branch=branch, status="DISPENSED").count()
    return render(request, "partials/factory_stats_cards.html", {
        "low_stock": low_stock, "expiring": expiring,
        "store_value": store_value, "processing": processing,
    })


@login_required
def factory_snapshot(request):
    """The tables behind the factory stat cards — reorder/expiry watch,
    finished goods on hand, and the most recent batches. Only meaningful
    once the dashboard is actually filtered to the factory branch."""
    branch = _selected_branch(request)
    biz, today = _biz(request), timezone.localdate()
    materials = list(RawMaterial.objects.filter(business=biz))
    for m in materials:
        m.stock = m.current_stock(branch=branch)
    low_stock = [m for m in materials if m.stock <= m.reorder_level]
    expiring = RawMaterialPurchase.objects.filter(
        raw_material__business=biz, branch=branch, remaining_quantity__gt=0,
        expiry_date__isnull=False, expiry_date__lte=today + timedelta(days=60),
    ).select_related("raw_material").order_by("expiry_date")[:10]
    factory_store = InventoryLocation.objects.filter(business=biz, branch=branch, type="PRODUCTION_STORE").first()
    store_items = StockItem.objects.filter(location=factory_store).select_related("product") \
        if factory_store else StockItem.objects.none()
    recent_batches = ProductionBatch.objects.filter(business=biz, branch=branch).select_related("product") \
        .order_by("-date", "-id")[:8]
    return render(request, "partials/factory_snapshot.html", {
        "low_stock": low_stock, "expiring": expiring,
        "store_items": store_items, "recent_batches": recent_batches,
    })


@login_required
def chart_daily_sales(request):
    """Line — last 30 days of sales, the owner's pulse."""
    biz, today = _biz(request), timezone.localdate()
    qs = _branch_filter(request, Sale.objects.filter(business=biz,
                        created_at__date__gte=today - timedelta(days=29)))
    by_day = {r["created_at__date"]: r["s"] for r in
              qs.values("created_at__date").annotate(s=Sum("total"))}
    days = [today - timedelta(days=i) for i in range(29, -1, -1)]
    return JsonResponse({"labels": [d.strftime("%d %b") for d in days],
                         "data": [float(by_day.get(d, 0)) for d in days]})


@login_required
def chart_sales_by_branch(request):
    """Bar — which outlet is pulling its weight (reps roll up under their branch)."""
    qs = _branch_filter(request, Sale.objects.filter(business=_biz(request))) \
        .values("location__branch__name").annotate(s=Sum("total"))
    return JsonResponse({"labels": [r["location__branch__name"] for r in qs],
                         "data": [float(r["s"]) for r in qs]})


@login_required
def chart_top_reps(request):
    """Horizontal bar — top 5 sales people by revenue."""
    qs = _branch_filter(request, Sale.objects.filter(business=_biz(request), location__type="REP")) \
        .values("location__rep__username").annotate(s=Sum("total")).order_by("-s")[:5]
    return JsonResponse({"labels": [r["location__rep__username"] for r in qs],
                         "data": [float(r["s"]) for r in qs]})


@login_required
def chart_payment_split(request):
    """Doughnut — cash vs card vs momo vs credit."""
    qs = _branch_filter(request, Sale.objects.filter(business=_biz(request))) \
        .values("payment_method").annotate(s=Sum("total"))
    return JsonResponse({"labels": [r["payment_method"] for r in qs],
                         "data": [float(r["s"]) for r in qs]})


@login_required
def chart_expenses_by_category(request):
    qs = _branch_filter(request, Expense.objects.filter(business=_biz(request))) \
        .values("category").annotate(s=Sum("amount"))
    return JsonResponse({"labels": [r["category"] for r in qs],
                         "data": [float(r["s"]) for r in qs]})


@login_required
def print_reports(request):
    """Printable line-item reports (sales, expenses, outstanding debtors) for a
    date range — the owner dashboard's charts summarize, this lists the detail."""
    biz, today = _biz(request), timezone.localdate()
    start = request.GET.get("start") or today.replace(day=1).isoformat()
    end = request.GET.get("end") or today.isoformat()

    sales = _branch_filter(request, Sale.objects.filter(
        business=biz, created_at__date__gte=start, created_at__date__lte=end
    ).select_related("location__branch").prefetch_related("items__product")).order_by("-created_at")
    expenses = _branch_filter(request, Expense.objects.filter(
        business=biz, date__gte=start, date__lte=end
    ).select_related("location__branch")).order_by("-date")
    debtors = _branch_filter(request, Debtor.objects.filter(business=biz).select_related("location__branch"))
    debtors = [d for d in debtors if d.balance() > 0]

    scope = "one branch" if request.GET.get("branch") else "all branches"
    return render(request, "reports/print_reports.html", {
        "branches": biz.branches.all(), "sales": sales, "expenses": expenses, "debtors": debtors,
        "start": start, "end": end,
        "sales_total": sales.aggregate(s=Sum("total"))["s"] or 0,
        "expenses_total": expenses.aggregate(s=Sum("amount"))["s"] or 0,
        "print_title": f"Report — {start} to {end} · {scope}",
    })


@login_required
def chart_monthly_gp(request):
    """Bar pair — 12 months of revenue vs gross profit (COGS = production cost)."""
    biz, today = _biz(request), timezone.localdate()
    start = (today.replace(day=1) - timedelta(days=365)).replace(day=1)
    items = _branch_filter(request, SaleItem.objects.filter(sale__business=biz, sale__created_at__date__gte=start),
                           path="sale__location__branch")
    rows = items.annotate(m=F("sale__created_at__month"), y=F("sale__created_at__year")) \
        .values("y", "m").annotate(rev=Sum("line_total"),
                                   gp=Sum(ExpressionWrapper(F("line_total") - F("quantity") * F("unit_cost"),
                                                            output_field=DecimalField())))
    by_key = {(r["y"], r["m"]): r for r in rows}
    labels, rev, gp = [], [], []
    d = start
    while d <= today:
        labels.append(d.strftime("%b %y"))
        r = by_key.get((d.year, d.month))
        rev.append(float(r["rev"]) if r else 0)
        gp.append(float(r["gp"]) if r else 0)
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return JsonResponse({"labels": labels, "revenue": rev, "gross_profit": gp})


@login_required
def production_report(request):
    """Owner-only: every batch ever run, cost and all."""
    batches = ProductionBatch.objects.filter(business=_biz(request)) \
        .select_related("product", "formula", "branch").order_by("-date", "-id")
    return render(request, "reports/production_report.html", {"batches": batches})


@login_required
def production_batch_detail(request, pk):
    """The whole story of one batch: formula, what was actually dispensed against
    it (FEFO-drawn from specific purchases), QA, and where the product has gone."""
    batch = get_object_or_404(ProductionBatch, pk=pk, business=_biz(request))
    dispensations = Dispensation.objects.filter(batch=batch).select_related("raw_material", "purchase")
    recent_distributions = DistributionLine.objects.filter(
        product=batch.product, distribution__business=_biz(request)
    ).select_related("distribution", "distribution__receiver_location__branch").order_by("-distribution__date")[:10]
    return render(request, "reports/production_batch_detail.html", {
        "batch": batch, "dispensations": dispensations,
        "qa": getattr(batch, "qa", None), "recent_distributions": recent_distributions,
    })


@login_required
@owner_required
def debtors_report(request):
    """Whole-business credit exposure — the number most likely to be worse
    than an owner thinks. Aged 0-30 / 31-60 / 60+ days from each debtor's
    oldest unpaid sale, largest debtors first."""
    biz, today = _biz(request), timezone.localdate()
    rows = []
    buckets = {"0_30": Decimal("0"), "31_60": Decimal("0"), "60_plus": Decimal("0")}
    for d in Debtor.objects.filter(business=biz).select_related("location__branch", "location__rep"):
        balance = d.balance()
        if balance <= 0:
            continue
        oldest = d.sales.filter(balance__gt=0).order_by("created_at").first()
        age_days = (today - oldest.created_at.date()).days if oldest else 0
        bucket = "0_30" if age_days <= 30 else ("31_60" if age_days <= 60 else "60_plus")
        buckets[bucket] += balance
        rows.append({"debtor": d, "balance": balance, "age_days": age_days, "bucket": bucket})
    rows.sort(key=lambda r: -r["balance"])
    total = sum(buckets.values(), Decimal("0"))

    return render(request, "reports/debtors_report.html", {
        "rows": rows, "buckets": buckets, "total": total,
        "largest": rows[:10], "print_title": "Debtors Report",
    })


@login_required
@owner_required
def rep_outlet_performance(request):
    """Ranked sales per outlet/rep, average sale value, and — the point of
    this report — stock they're holding right now vs what they've actually
    sold. A location sitting on a lot of value with little recent sales is
    exactly what this is meant to expose."""
    biz = _biz(request)
    date_from = request.GET.get("from", "")
    date_to = request.GET.get("to", "")

    sales_qs = Sale.objects.filter(business=biz, location__type__in=("OUTLET", "REP"))
    if date_from:
        sales_qs = sales_qs.filter(created_at__date__gte=date_from)
    if date_to:
        sales_qs = sales_qs.filter(created_at__date__lte=date_to)
    sales_by_location = {
        r["location_id"]: r for r in
        sales_qs.values("location_id").annotate(total=Sum("total"), count=Count("id"))
    }

    stock_value_expr = ExpressionWrapper(F("quantity") * F("buying_price"), output_field=DecimalField(max_digits=16, decimal_places=2))
    stock_by_location = {
        r["location_id"]: r["v"] or Decimal("0") for r in
        StockItem.objects.filter(location__business=biz, location__type__in=("OUTLET", "REP"))
        .values("location_id").annotate(v=Sum(stock_value_expr))
    }

    rows = []
    for loc in InventoryLocation.objects.filter(business=biz, type__in=("OUTLET", "REP")).select_related("branch", "rep"):
        sales_data = sales_by_location.get(loc.id, {"total": Decimal("0"), "count": 0})
        total_sales = sales_data["total"] or Decimal("0")
        count = sales_data["count"] or 0
        stock_value = stock_by_location.get(loc.id, Decimal("0"))
        rows.append({
            "location": loc, "total_sales": total_sales, "sale_count": count,
            "avg_sale": (total_sales / count) if count else Decimal("0"),
            "stock_value": stock_value,
            # a location holding more stock value than it has sold in this window
            # is a real flag — not proof of a problem, but worth the owner's eye
            "sitting_on_stock": stock_value > total_sales and stock_value > 0,
        })
    rows.sort(key=lambda r: -r["total_sales"])

    return render(request, "reports/rep_outlet_performance.html", {
        "rows": rows, "date_from": date_from, "date_to": date_to,
        "print_title": "Rep & Outlet Performance",
    })


@login_required
@owner_required
def product_receipts(request, pk):
    """Every receipt that contributed to one product's revenue — reached by
    clicking a product on the Revenue by Product report."""
    biz = _biz(request)
    product = get_object_or_404(Product, pk=pk, business=biz)
    date_from = request.GET.get("from", "")
    date_to = request.GET.get("to", "")

    items = SaleItem.objects.filter(product=product, sale__business=biz) \
        .select_related("sale", "sale__location__branch", "sale__served_by").order_by("-sale__created_at")
    if date_from:
        items = items.filter(sale__created_at__date__gte=date_from)
    if date_to:
        items = items.filter(sale__created_at__date__lte=date_to)

    total_revenue = items.aggregate(s=Sum("line_total"))["s"] or Decimal("0")
    total_qty = items.aggregate(s=Sum("quantity"))["s"] or 0
    page_obj = Paginator(items, 25).get_page(request.GET.get("page"))

    return render(request, "reports/product_receipts.html", {
        "product": product, "page_obj": page_obj, "date_from": date_from, "date_to": date_to,
        "total_revenue": total_revenue, "total_qty": total_qty,
        "print_title": f"Receipts — {product.name}",
    })
