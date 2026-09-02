"""
Owner graphs & stats — Ternah for Factories.
Every endpoint returns JSON for Chart.js; the dashboard shell loads
each card lazily with HTMX. Everything is branch-aware so the owner's
per-branch view 'makes sense': users -> branch, rep inventories -> branch,
sales/expenses/debtors -> location -> branch.
"""
from datetime import timedelta
from django.contrib.auth.decorators import login_required
from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from production.models import Dispensation, DistributionLine, ProductionBatch
from sales.models import Debtor, Expense, Sale, SaleItem


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
    return render(request, "reports/owner_dashboard.html", {"branches": biz.branches.all()})


@login_required
def stats_cards(request):
    """Headline numbers: today, MTD, gross profit MTD, outstanding debt."""
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

    return render(request, "reports/print_reports.html", {
        "branches": biz.branches.all(), "sales": sales, "expenses": expenses, "debtors": debtors,
        "start": start, "end": end,
        "sales_total": sales.aggregate(s=Sum("total"))["s"] or 0,
        "expenses_total": expenses.aggregate(s=Sum("amount"))["s"] or 0,
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
        .select_related("product", "formula").order_by("-date", "-id")
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
