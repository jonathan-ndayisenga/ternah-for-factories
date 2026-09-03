"""
Seed a full demo tenant end-to-end: business -> branches -> users -> raw
materials -> formulas -> production batches -> distributions -> inventory ->
sales/debtors/expenses. Idempotent on the business slug: re-running clears
and rebuilds just this tenant's data so you can iterate freely.

    python manage.py seed_demo_data
"""
import random
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from core.models import Branch
from finance.models import Supplier
from platformadmin.models import AuditLog, Business, Module, ModuleSubscription, SubscriptionExtension
from platformadmin.services import delete_business_completely
from production.models import (
    Category, Distribution, DistributionLine, Dispensation, FormulaLine,
    Product, ProductFormula, ProductionBatch, QAReport, RawMaterial,
    RawMaterialPurchase,
)
from sales.models import (
    Debtor, DebtorPayment, Expense, InventoryLocation, MomoAccount, PendingAction,
    Sale, SaleItem, StockItem, StockMovement,
)

FACTORY_MODULES = ["PRODUCTION", "SALES", "DEBTORS", "EXPENSES", "FINANCE", "REPORTS"]
BUSINESS_NAME = "Ternah Cosmetics Ltd"
BUSINESS_SLUG = "ternah-cosmetics"

PASSWORD = "Demo@2026"


class Command(BaseCommand):
    help = "Seed a realistic demo tenant (business, branches, users, stock, sales, expenses)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=45, help="How many days of sales/expense history to generate.")

    def handle(self, *args, **opts):
        random.seed(42)
        days = opts["days"]

        with transaction.atomic():
            self._wipe_existing()
            biz = self._make_business()
            branches = self._make_branches(biz)
            users = self._make_users(biz, branches)
            raw_materials = self._make_raw_materials(biz)
            categories, products = self._make_catalog(biz)
            formulas = self._make_formulas(products, raw_materials, users["production"])
            locations = self._make_inventory_locations(biz, branches, users)
            self._make_batches_and_distribution(biz, products, formulas, raw_materials, locations, branches, users)
            momo = MomoAccount.objects.create(business=biz, provider="MTN MoMo", number="0771234567")
            self._make_sales_and_expenses(biz, locations, users, days, momo)
            self._make_pending_actions(biz, users)

        self._print_credentials(biz, users)

    # ------------------------------------------------------------------

    def _wipe_existing(self):
        biz = Business.objects.filter(slug=BUSINESS_SLUG).first()
        if biz:
            delete_business_completely(biz)

    def _make_business(self):
        biz = Business.objects.create(
            name=BUSINESS_NAME, slug=BUSINESS_SLUG,
            contact_email="owner@ternahcosmetics.example",
            contact_phone="0700111222", address="Plot 14, Nakawa Industrial Area, Kampala",
            subscription_expires_at=timezone.now() + timedelta(days=365),
        )
        for code in FACTORY_MODULES:
            module, _ = Module.objects.get_or_create(code=code, defaults={"name": code.title()})
            ModuleSubscription.objects.create(business=biz, module=module)
        SubscriptionExtension.objects.create(business=biz, months=12, reason="Demo seed — initial subscription")
        AuditLog.write("TENANT_CREATED", business=biz, description=f"Seeded demo tenant {biz.name}")
        return biz

    def _make_branches(self, biz):
        factory = Branch.objects.create(business=biz, name="Factory", kind="FACTORY", address="Nakawa Industrial Area")
        kampala = Branch.objects.create(business=biz, name="Kampala Outlet", kind="OUTLET", address="William Street, Kampala")
        mbarara = Branch.objects.create(business=biz, name="Mbarara Outlet", kind="OUTLET", address="High Street, Mbarara")
        return {"factory": factory, "kampala": kampala, "mbarara": mbarara}

    def _make_users(self, biz, branches):
        User = get_user_model()

        def make(username, role, branch, **extra):
            u = User.objects.create_user(username=username, password=PASSWORD, business=biz,
                                         branch=branch, role=role, **extra)
            return u

        owner = make("owner.ternah", "OWNER", None, first_name="Grace", last_name="Nabatanzi")
        manager = make("manager.kampala", "MANAGER", branches["kampala"], first_name="Peter",
                       last_name="Okello", can_swap=True, can_refund=True)
        cashier = make("cashier.kampala", "CASHIER", branches["kampala"], first_name="Sarah", last_name="Auma",
                      allowed_tiers=["RETAIL", "WHOLESALE"])
        rep = make("rep.esther", "SALES_REP", branches["mbarara"], first_name="Esther", last_name="Kirabo",
                   allowed_tiers=["RETAIL", "WHOLESALE"])
        production = make("prod.moses", "PRODUCTION", branches["factory"], first_name="Moses", last_name="Byaruhanga")

        modules = {m.code: m for m in Module.objects.filter(code__in=FACTORY_MODULES)}
        manager.modules.set(modules.values())
        cashier.modules.set([modules["SALES"], modules["DEBTORS"], modules["EXPENSES"]])
        rep.modules.set([modules["SALES"], modules["DEBTORS"]])
        production.modules.set([modules["PRODUCTION"]])

        return {"owner": owner, "manager": manager, "cashier": cashier, "rep": rep, "production": production}

    def _make_raw_materials(self, biz):
        specs = [
            ("Petroleum Jelly Base", "kg", Decimal("3000")),
            ("Fragrance Oil", "L", Decimal("45000")),
            ("Preservative", "L", Decimal("60000")),
            ("Caustic Soda", "kg", Decimal("4500")),
            ("Palm Oil", "L", Decimal("6500")),
            ("Empty Tins 30g", "pcs", Decimal("350")),
            ("Bar Soap Wrappers", "pcs", Decimal("120")),
        ]
        supplier = Supplier.objects.create(business=biz, name="Kampala Chemicals Ltd", phone="0752000111")
        materials = {}
        start = timezone.localdate() - timedelta(days=120)
        for name, uom, unit_cost in specs:
            rm = RawMaterial.objects.create(business=biz, name=name, unit_of_measure=uom,
                                            reorder_level=Decimal("10"))
            qty = Decimal("500") if uom in ("kg", "L") else Decimal("2000")
            RawMaterialPurchase.objects.create(
                raw_material=rm, quantity=qty, total_cost=(qty * unit_cost).quantize(Decimal("0.01")),
                remaining_quantity=qty, purchase_date=start, batch_number=f"RM-{slugify(name)[:6].upper()}-001",
                expiry_date=start + timedelta(days=540), supplier=supplier, on_credit=False,
            )
            materials[name] = rm
        return materials

    def _make_catalog(self, biz):
        cat_names = ["Petroleum Jelly", "Body Lotion", "Bar Soap"]
        categories = {n: Category.objects.create(business=biz, name=n) for n in cat_names}
        products_spec = [
            ("Petroleum Jelly 30g", "Petroleum Jelly", "30 g", "PJ-030", Decimal("3000"), Decimal("2500")),
            ("Petroleum Jelly 100g", "Petroleum Jelly", "100 g", "PJ-100", Decimal("8000"), Decimal("6800")),
            ("Body Lotion 500ml", "Body Lotion", "500 ml", "BL-500", Decimal("15000"), Decimal("13000")),
            ("Bar Soap 200g", "Bar Soap", "200 g", "BS-200", Decimal("4000"), Decimal("3400")),
        ]
        products = []
        for name, cat, pack, sku, retail, wholesale in products_spec:
            p = Product.objects.create(business=biz, category=categories[cat], name=name, pack_size=pack, sku=sku,
                                       retail_price=retail, wholesale_price=wholesale)   # save() flips it ACTIVE
            products.append(p)
        return categories, products

    def _make_formulas(self, products, rm, author):
        recipe_lines = {
            "PJ-030": [("Petroleum Jelly Base", Decimal("0.028")), ("Fragrance Oil", Decimal("0.0005")),
                      ("Empty Tins 30g", Decimal("1"))],
            "PJ-100": [("Petroleum Jelly Base", Decimal("0.095")), ("Fragrance Oil", Decimal("0.0015")),
                      ("Empty Tins 30g", Decimal("1"))],
            "BL-500": [("Palm Oil", Decimal("0.30")), ("Fragrance Oil", Decimal("0.01")),
                      ("Preservative", Decimal("0.005"))],
            "BS-200": [("Caustic Soda", Decimal("0.03")), ("Palm Oil", Decimal("0.15")),
                      ("Bar Soap Wrappers", Decimal("1"))],
        }
        formulas = {}
        now = timezone.now()
        for product in products:
            formula = ProductFormula.objects.create(product=product, version=1, status="APPROVED",
                                                     approved_by=author, approved_at=now)
            for rm_name, qty in recipe_lines[product.sku]:
                FormulaLine.objects.create(formula=formula, raw_material=rm[rm_name], quantity_per_unit=qty)
            formulas[product.sku] = formula
        return formulas

    def _make_inventory_locations(self, biz, branches, users):
        factory_store = InventoryLocation.objects.create(business=biz, branch=branches["factory"], type="PRODUCTION_STORE")
        kampala_outlet = InventoryLocation.objects.create(business=biz, branch=branches["kampala"], type="OUTLET")
        rep_loc = InventoryLocation.objects.create(business=biz, branch=branches["mbarara"], type="REP", rep=users["rep"])
        return {"factory_store": factory_store, "kampala_outlet": kampala_outlet, "rep": rep_loc}

    def _make_batches_and_distribution(self, biz, products, formulas, rm, locations, branches, users):
        batch_date = timezone.localdate() - timedelta(days=30)

        for i, product in enumerate(products, start=1):
            formula = formulas[product.sku]
            target = 200
            batch = ProductionBatch.objects.create(
                business=biz, product=product, formula=formula,
                batch_number=f"BATCHMB-{batch_date.strftime('%d%m%y')}-{i:02d}",
                date=batch_date, target_quantity=target, actual_quantity=target,
                status="COMPLETED", created_by=users["production"],
            )
            unit_cost = Decimal("0")
            for material, qty_per_unit in formula.lines.values_list("raw_material", "quantity_per_unit"):
                material_obj = RawMaterial.objects.get(pk=material)
                needed = qty_per_unit * target
                purchase = material_obj.purchases.order_by("purchase_date").first()
                Dispensation.objects.create(batch=batch, raw_material=material_obj, purchase=purchase,
                                            quantity_dispensed=needed)
                purchase.remaining_quantity -= needed
                purchase.save(update_fields=["remaining_quantity"])
                unit_cost += qty_per_unit * purchase.unit_cost
            batch.unit_cost_at_production = unit_cost.quantize(Decimal("0.0001"))
            batch.total_cost = (unit_cost * target).quantize(Decimal("0.01"))
            batch.save(update_fields=["unit_cost_at_production", "total_cost"])
            QAReport.objects.create(batch=batch, quality_notes="Passed viscosity and scent check.",
                                    quantity_notes=f"{target} units confirmed.", author=users["production"])

            # land the whole batch in the factory finished-goods store first
            StockItem.objects.create(location=locations["factory_store"], product=product, quantity=target,
                                      buying_price=batch.unit_cost_at_production, low_stock_threshold=20)
            StockMovement.objects.create(location=locations["factory_store"], product=product, quantity=target,
                                         reason="DISTRIBUTION", reference=batch.batch_number)

            # distribute a slice to the outlet and to the rep
            outlet_qty, rep_qty = int(target * 0.6), int(target * 0.25)
            for i2, (dest, qty) in enumerate([(locations["kampala_outlet"], outlet_qty),
                                              (locations["rep"], rep_qty)], start=1):
                dist = Distribution.objects.create(
                    business=biz, receiver_location=dest,
                    delivery_note_number=f"DNMB-{batch_date.strftime('%d%m%y')}-{i}{i2}",
                    date=batch_date + timedelta(days=1), status="RECEIVED", created_by=users["production"],
                )
                DistributionLine.objects.create(distribution=dist, product=product, quantity=qty)
                StockItem.objects.update_or_create(
                    location=dest, product=product,
                    defaults={"buying_price": batch.unit_cost_at_production, "low_stock_threshold": 10},
                )
                item = StockItem.objects.get(location=dest, product=product)
                item.quantity += qty
                item.save(update_fields=["quantity"])
                StockMovement.objects.create(location=dest, product=product, quantity=qty,
                                             reason="DISTRIBUTION", reference=dist.delivery_note_number)
                # remove distributed qty from the factory store
                fstore_item = StockItem.objects.get(location=locations["factory_store"], product=product)
                fstore_item.quantity -= qty
                fstore_item.save(update_fields=["quantity"])
                StockMovement.objects.create(location=locations["factory_store"], product=product, quantity=-qty,
                                             reason="DISTRIBUTION", reference=dist.delivery_note_number)

    def _make_sales_and_expenses(self, biz, locations, users, days, momo):
        methods = ["CASH", "CASH", "MOBILE_MONEY", "MOBILE_MONEY", "CREDIT", "CASH"]
        sell_locations = [
            (locations["kampala_outlet"], users["cashier"]),
            (locations["rep"], users["rep"]),
        ]
        debtors = [
            Debtor.objects.create(business=biz, location=locations["kampala_outlet"], name="Amina Nakato", phone="0782001100"),
            Debtor.objects.create(business=biz, location=locations["rep"], name="John Ssemwogerere", phone="0752003344"),
        ]
        walk_in_names = ["Brenda Nansubuga", "Kato Ronald", "Fiona Achieng", "David Mugisha",
                        "Patience Adikini", "Isaac Wamala", "Grace Alupo", "Tom Kirumira"]
        expense_specs = [("Rent", Decimal("400000")), ("Utilities", Decimal("80000")),
                         ("Transport", Decimal("50000")), ("Salaries", Decimal("600000")),
                         ("Packaging", Decimal("35000"))]

        seq = 1
        today = timezone.localdate()
        for day_offset in range(days, -1, -1):
            date = today - timedelta(days=day_offset)
            for location, server in sell_locations:
                stock_items = list(StockItem.objects.filter(location=location, quantity__gt=0))
                if not stock_items or random.random() > 0.75:
                    continue
                n_sales = random.randint(1, 3)
                for _ in range(n_sales):
                    item = random.choice(stock_items)
                    if item.quantity < 1 or item.product.retail_price is None:
                        continue
                    qty = min(item.quantity, random.randint(1, 5))
                    method = random.choice(methods)
                    subtotal = (item.product.retail_price * qty).quantize(Decimal("0.01"))
                    debtor = random.choice(debtors) if method == "CREDIT" else None
                    customer_name = debtor.name if debtor else random.choice(walk_in_names)
                    customer_phone = debtor.phone if debtor else ""
                    amount_paid = Decimal("0") if method == "CREDIT" else subtotal
                    balance = subtotal - amount_paid
                    sale = Sale.objects.create(
                        business=biz, location=location,
                        receipt_number=f"RCP-{date.strftime('%d%m%y')}-{seq:04d}",
                        served_by=server, payment_method=method,
                        paid_into_momo=momo if method == "MOBILE_MONEY" else None,
                        debtor=debtor, customer_name=customer_name, customer_phone=customer_phone,
                        subtotal=subtotal, total=subtotal,
                        amount_paid=amount_paid, balance=balance,
                    )
                    sale.created_at = timezone.make_aware(
                        timezone.datetime.combine(date, timezone.datetime.min.time().replace(hour=random.randint(9, 18)))
                    )
                    sale.save(update_fields=["created_at"])
                    SaleItem.objects.create(sale=sale, product=item.product, quantity=qty,
                                            unit_price=item.product.retail_price, unit_cost=item.buying_price,
                                            line_total=subtotal)
                    item.quantity -= qty
                    item.save(update_fields=["quantity"])
                    movement = StockMovement.objects.create(location=location, product=item.product, quantity=-qty,
                                                            reason="SALE", reference=sale.receipt_number)
                    movement.created_at = sale.created_at
                    movement.save(update_fields=["created_at"])
                    seq += 1

            if day_offset % 7 == 0:
                cat, amt = random.choice(expense_specs)
                Expense.objects.create(business=biz, location=locations["kampala_outlet"], category=cat,
                                       amount=amt, note=f"{cat} — {date:%B}", date=date,
                                       recorded_by=users["manager"])

        for debtor in debtors:
            owed = debtor.balance()
            if owed:
                pay = (owed * Decimal("0.4")).quantize(Decimal("0.01"))
                if pay > 0:
                    DebtorPayment.objects.create(debtor=debtor, amount=pay, method="CASH",
                                                 received_by=users["manager"])
                    remaining = pay
                    for open_sale in debtor.sales.filter(balance__gt=0).order_by("created_at"):
                        if remaining <= 0:
                            break
                        applied = min(remaining, open_sale.balance)
                        open_sale.balance -= applied
                        open_sale.amount_paid += applied
                        open_sale.save(update_fields=["balance", "amount_paid"])
                        remaining -= applied

    def _make_pending_actions(self, biz, users):
        PendingAction.objects.create(
            business=biz, action_type="SWAP", requested_by=users["cashier"],
            payload={"customer": "Amina Nakato", "original_item": "Bar Soap 200g",
                    "replacement_item": "Body Lotion 500ml", "reason": "Wrong item given at sale"},
        )
        PendingAction.objects.create(
            business=biz, action_type="STOCK_REQUEST", requested_by=users["cashier"],
            payload={"product": "Petroleum Jelly 30g", "quantity": "50",
                    "note": "Running low before the weekend rush"},
        )

    def _print_credentials(self, biz, users):
        self.stdout.write(self.style.SUCCESS(f"\nSeeded '{biz.name}' (slug={biz.slug}) with a full demo dataset.\n"))
        self.stdout.write("Log in at /accounts/login/ with any of:\n")
        rows = [
            ("Platform superadmin", "superadmin", "change-me-now", "/platform/ (run seed_superadmin first if missing)"),
            ("Business owner", users["owner"].username, PASSWORD, "/reports/ owner dashboard"),
            ("Branch manager (Kampala)", users["manager"].username, PASSWORD, "manager view switcher"),
            ("Cashier (Kampala)", users["cashier"].username, PASSWORD, "POS"),
            ("Sales rep (Mbarara)", users["rep"].username, PASSWORD, "rep POS"),
            ("Production", users["production"].username, PASSWORD, "batches/QA"),
        ]
        width = max(len(r[0]) for r in rows)
        for label, username, password, note in rows:
            self.stdout.write(f"  {label.ljust(width)}  user: {username:<18} pass: {password:<12} {note}")
        self.stdout.write("")
