from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from core.models import Branch
from platformadmin.models import Business
from sales.models import Debtor, InventoryLocation, Sale


class DebtCollectionBehaviorTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(
            name="Ternah Factory",
            slug="ternah-factory",
            contact_email="hello@example.com",
        )
        self.branch = Branch.objects.create(
            business=self.business,
            name="Main Branch",
            kind="OUTLET",
        )
        self.manager = User.objects.create_user(
            username="manager",
            password="pass123",
            business=self.business,
            branch=self.branch,
            role="MANAGER",
        )
        self.location = InventoryLocation.objects.create(
            business=self.business,
            branch=self.branch,
            type="OUTLET",
        )
        self.debtor = Debtor.objects.create(
            business=self.business,
            location=self.location,
            name="Moses Kato",
            phone="0772000000",
        )
        Sale.objects.create(
            business=self.business,
            location=self.location,
            receipt_number="RCP-001",
            served_by=self.manager,
            payment_method="CREDIT",
            debtor=self.debtor,
            customer_name="Moses Kato",
            subtotal=Decimal("140000"),
            total=Decimal("140000"),
            amount_paid=Decimal("0"),
            balance=Decimal("140000"),
        )

    def test_manager_debt_collection_form_does_not_prefill_full_balance(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("manager:debtors"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'value="140000"')
        self.assertContains(response, 'placeholder="Amount (UGX)"')
