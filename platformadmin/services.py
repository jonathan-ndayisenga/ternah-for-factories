"""Tenant teardown. Business.delete() can't cascade cleanly on its own —
several models use on_delete=PROTECT specifically to stop accidental data
loss elsewhere in the app (Category<-Product, RawMaterial<-Dispensation,
LedgerAccount<-JournalLine, ...), which blocks even when the protecting row
would itself be destroyed in the same operation. Delete leaves-first, in
dependency order, then the business itself.

AuditLog.business is on_delete=SET_NULL by design — the platform keeps a
record that this tenant existed and was removed, it just stops pointing at
a live Business row once this runs."""
from django.db import transaction
from django.db.models import Q


def delete_business_completely(biz):
    from core.models import Branch
    from finance.models import (
        Disbursement, FinancialAccount, JournalEntry, JournalLine, LedgerAccount, Supplier, SupplierPayable,
    )
    from production.models import (
        Category, Dispensation, Distribution, DistributionLine, FormulaLine, Product, ProductFormula,
        ProductionBatch, QAReport, RawMaterial, RawMaterialPurchase,
    )
    from sales.models import (
        BankAccount, DailyOpeningBalance, Debtor, DebtorPayment, Expense, InventoryLocation, MomoAccount,
        PendingAction, Sale, SaleItem, StockItem, StockMovement, StockRequest, WholesaleOrder,
    )

    with transaction.atomic():
        SaleItem.objects.filter(sale__business=biz).delete()
        DebtorPayment.objects.filter(debtor__business=biz).delete()
        Sale.objects.filter(business=biz).delete()
        Debtor.objects.filter(business=biz).delete()
        WholesaleOrder.objects.filter(business=biz).delete()
        Expense.objects.filter(business=biz).delete()
        DailyOpeningBalance.objects.filter(business=biz).delete()
        StockMovement.objects.filter(location__business=biz).delete()
        StockItem.objects.filter(location__business=biz).delete()
        DistributionLine.objects.filter(distribution__business=biz).delete()
        Distribution.objects.filter(business=biz).delete()
        QAReport.objects.filter(batch__business=biz).delete()
        Dispensation.objects.filter(batch__business=biz).delete()
        ProductionBatch.objects.filter(business=biz).delete()
        FormulaLine.objects.filter(formula__product__business=biz).delete()
        ProductFormula.objects.filter(product__business=biz).delete()
        Product.objects.filter(business=biz).delete()
        Category.objects.filter(business=biz).delete()
        RawMaterialPurchase.objects.filter(raw_material__business=biz).delete()
        RawMaterial.objects.filter(business=biz).delete()
        StockRequest.objects.filter(business=biz).delete()
        PendingAction.objects.filter(business=biz).delete()
        InventoryLocation.objects.filter(business=biz).delete()
        MomoAccount.objects.filter(business=biz).delete()
        BankAccount.objects.filter(business=biz).delete()

        JournalLine.objects.filter(entry__business=biz).delete()
        JournalEntry.objects.filter(business=biz).delete()
        LedgerAccount.objects.filter(business=biz).delete()
        Disbursement.objects.filter(Q(from_account__business=biz) | Q(to_account__business=biz)).delete()
        FinancialAccount.objects.filter(business=biz).delete()
        SupplierPayable.objects.filter(supplier__business=biz).delete()
        Supplier.objects.filter(business=biz).delete()

        Branch.objects.filter(business=biz).delete()
        biz.delete()   # remaining children (User, ModuleSubscription, SubscriptionExtension) are plain CASCADE
