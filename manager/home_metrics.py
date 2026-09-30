"""One number per Home tile, for the Manager role — see the Home Redesign
developer spec. The two Production functions take `request` (they need the
session-based factory picker); the rest take the manager `user` directly
and are scoped to the manager's own branch. Kept separate from
manager/views.py so the same computation backs both the first
server-rendered paint and the /home/tiles/<tile>/ HTMX refresh, without
them drifting apart.

Two of these (production, finance) answer the spec's own "open questions"
with what the schema can actually support today — see each function's
docstring for the specific fallback used.
"""
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone


def _factory_branch(request):
    """Reuses production's own session-based factory picker rather than
    assuming request.user.branch — a manager acting as Production doesn't
    belong to a factory branch themselves (see production.views._current_
    factory's own docstring); with one factory, the common case, this is
    transparent."""
    from production.views import _current_factory
    return _current_factory(request)


def production_batches(request):
    """'Batches in progress' = started but not yet completed, at the
    factory this Production session is currently working in."""
    from production.models import ProductionBatch
    factory = _factory_branch(request)
    if not factory:
        return 0
    return ProductionBatch.objects.filter(
        business=request.user.business, branch=factory, status__in=("PLANNED", "DISPENSED")).count()


def formulas_short_of_materials(request):
    """The spec's first open question: can Production already tell a
    formula is short? Not as a standing figure — but ProductionBatch.
    requirements() gives per-batch need, so this compares each approved
    formula's most recent batch size (at the current factory) against
    RawMaterial.current_stock() there. A formula that has never been run
    has nothing to size the comparison against, so it's skipped rather than
    guessed at (an honest partial answer, not a hard '0')."""
    from production.models import ProductFormula, ProductionBatch
    factory = _factory_branch(request)
    if not factory:
        return 0
    short = 0
    formulas = ProductFormula.objects.filter(
        product__business=request.user.business, status="APPROVED", is_active=True).select_related("product")
    for formula in formulas:
        last_batch = ProductionBatch.objects.filter(
            business=request.user.business, branch=factory, product=formula.product
        ).order_by("-created_at").first()
        if not last_batch:
            continue
        is_short = False
        for line in formula.lines.select_related("raw_material"):
            needed = line.quantity_per_unit * last_batch.target_quantity
            if needed > line.raw_material.current_stock(branch=factory):
                is_short = True
                break
        if is_short:
            short += 1
    return short


def branch_stock_awaiting_approval(user):
    """Same figure as the sidebar's Branch badge (pending_inventory +
    pending_approvals in accounts.context_processors) — recomputed here
    rather than imported, so this module has no dependency on that one."""
    from production.models import Distribution
    from sales.models import OutletTransfer, PendingAction, StockReturn
    branch = user.branch
    pending_inventory = (
        Distribution.objects.filter(business=user.business, status="SENT", receiver_location__branch=branch).count()
        + OutletTransfer.objects.filter(business=user.business, status="SENT", to_location__branch=branch).count()
        + StockReturn.objects.filter(business=user.business, status__in=["SENT", "DISPUTED"], to_location__branch=branch).count()
    )
    pending_approvals = PendingAction.objects.filter(
        business=user.business, status="PENDING", requested_by__branch=branch).count()
    return pending_inventory + pending_approvals


def sales_today(user):
    """Cashier tile — today's completed sales at this branch's outlet,
    UGX. Reversed sales never counted, same rule as everywhere else."""
    from sales.models import InventoryLocation, Sale
    outlet = InventoryLocation.objects.filter(branch=user.branch, type="OUTLET").first()
    if not outlet:
        return Decimal("0")
    total = Sale.objects.filter(
        location=outlet, created_at__date=timezone.localdate(), is_reversed=False
    ).aggregate(s=Sum("total"))["s"]
    return total or Decimal("0")


def debtors_owing(user):
    """The spec's second open question: 'debtors overdue' needs a due
    date, and Debtor has none (only finance.Invoice does, a much smaller,
    separate AR population) — so per the spec's own stated fallback, this
    shows the count that does exist: debtors currently owing anything, at
    this branch."""
    from sales.models import Debtor
    count = 0
    for d in Debtor.objects.filter(business=user.business, location__branch=user.branch):
        if d.balance() > 0:
            count += 1
    return count
