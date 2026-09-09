from decimal import Decimal

from django.db import migrations


def backfill(apps, schema_editor):
    """Reconstructed from what's provable today: a debtor's current live
    balance, plus every later payment's amount added back on — since each
    payment only ever reduces the balance by its own amount, this recovers
    the true balance-as-it-stood at each earlier payment too."""
    Debtor = apps.get_model("sales", "Debtor")
    DebtorPayment = apps.get_model("sales", "DebtorPayment")

    for debtor in Debtor.objects.all():
        current_balance = sum((s.balance for s in debtor.sales.all()), Decimal("0"))
        payments = list(DebtorPayment.objects.filter(debtor=debtor).order_by("-created_at"))
        running_later_total = Decimal("0")
        for p in payments:
            p.balance_after = current_balance + running_later_total
            p.save(update_fields=["balance_after"])
            running_later_total += p.amount


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0008_debtorpayment_balance_after'),
    ]

    operations = [
        migrations.RunPython(backfill, noop),
    ]
