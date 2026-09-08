import django.db.models.deletion
from django.db import migrations, models


def backfill_branches(apps, schema_editor):
    """Every business today has exactly one Factory branch (multi-factory
    is brand new) — point all existing purchases/batches/distributions at
    it. A business with no Factory branch yet (shouldn't exist, but just in
    case) gets one created so the backfill always has somewhere to land."""
    Business = apps.get_model("platformadmin", "Business")
    Branch = apps.get_model("core", "Branch")
    RawMaterialPurchase = apps.get_model("production", "RawMaterialPurchase")
    ProductionBatch = apps.get_model("production", "ProductionBatch")
    Distribution = apps.get_model("production", "Distribution")

    for business in Business.objects.all():
        factory = Branch.objects.filter(business=business, kind="FACTORY").order_by("id").first()
        if not factory:
            has_data = (RawMaterialPurchase.objects.filter(raw_material__business=business).exists()
                       or ProductionBatch.objects.filter(business=business).exists()
                       or Distribution.objects.filter(business=business).exists())
            if not has_data:
                continue
            factory = Branch.objects.create(business=business, name="Factory", kind="FACTORY")
        RawMaterialPurchase.objects.filter(raw_material__business=business, branch__isnull=True) \
            .update(branch=factory)
        ProductionBatch.objects.filter(business=business, branch__isnull=True).update(branch=factory)
        Distribution.objects.filter(business=business, sender_branch__isnull=True).update(sender_branch=factory)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('production', '0015_distribution_sender_branch_productionbatch_branch_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill_branches, noop),
        migrations.AlterField(
            model_name='rawmaterialpurchase',
            name='branch',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='raw_material_purchases', to='core.branch'),
        ),
        migrations.AlterField(
            model_name='productionbatch',
            name='branch',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='production_batches', to='core.branch'),
        ),
        migrations.AlterField(
            model_name='distribution',
            name='sender_branch',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='distributions_sent', to='core.branch'),
        ),
    ]
