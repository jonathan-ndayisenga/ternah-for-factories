"""One-off: post journal entries for business events that happened before the
double-entry ledger existed. Safe to re-run any time — every post_* call in
finance.services is idempotent on source_ref, so already-posted events are
silently skipped."""
from django.core.management.base import BaseCommand

from finance.services import (
    post_batch_completion, post_debtor_payment, post_expense, post_raw_material_purchase, post_sale,
)
from production.models import ProductionBatch, RawMaterialPurchase
from sales.models import DebtorPayment, Expense, Sale


class Command(BaseCommand):
    help = "Post journal entries for existing sales, payments, expenses, purchases and completed batches."

    def handle(self, *args, **options):
        counts = {"sales": 0, "payments": 0, "expenses": 0, "purchases": 0, "batches": 0}

        for sale in Sale.objects.select_related("location__branch").prefetch_related("items"):
            if post_sale(sale):
                counts["sales"] += 1

        for payment in DebtorPayment.objects.select_related("debtor__location__branch"):
            if post_debtor_payment(payment):
                counts["payments"] += 1

        for expense in Expense.objects.select_related("location__branch"):
            if post_expense(expense):
                counts["expenses"] += 1

        for purchase in RawMaterialPurchase.objects.select_related("raw_material"):
            if post_raw_material_purchase(purchase):
                counts["purchases"] += 1

        for batch in ProductionBatch.objects.filter(status="COMPLETED"):
            if post_batch_completion(batch):
                counts["batches"] += 1

        self.stdout.write(self.style.SUCCESS(
            f"Posted: {counts['sales']} sales, {counts['payments']} debtor payments, "
            f"{counts['expenses']} expenses, {counts['purchases']} raw material purchases, "
            f"{counts['batches']} completed batches."
        ))
