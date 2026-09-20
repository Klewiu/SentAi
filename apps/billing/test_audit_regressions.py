from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User, UserPlanTier
from apps.billing.access import publication_users
from apps.billing.models import BillingInvoice, BillingPayment, ManualPlanOrder


class BillingAuditRegressionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="audit-customer",
            email="audit-customer@example.com",
            password="strong-pass-123",
            plan_tier=UserPlanTier.PRO,
        )

    def invoice(self, number, **kwargs):
        return BillingInvoice.objects.create(
            user=self.user,
            invoice_number=number,
            issued_at=timezone.localdate(),
            document=SimpleUploadedFile(f"{number}.pdf", b"%PDF-1.4 audit"),
            **kwargs,
        )

    def test_inactive_paid_plan_user_has_no_publication_access(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        self.assertFalse(publication_users().filter(pk=self.user.pk).exists())

    def test_invoice_number_is_unique(self):
        self.invoice("AUDIT-UNIQUE")

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.invoice("AUDIT-UNIQUE")

    def test_one_invoice_per_stripe_payment(self):
        payment = BillingPayment.objects.create(
            user=self.user,
            stripe_invoice_id="in_audit_unique",
            amount_paid=10000,
            currency="pln",
            status="paid",
        )
        self.invoice("AUDIT-PAYMENT-1", payment=payment)

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.invoice("AUDIT-PAYMENT-2", payment=payment)

    def test_one_invoice_per_manual_order(self):
        order = ManualPlanOrder.objects.create(
            user=self.user,
            tier=UserPlanTier.PRO,
            amount=40000,
            currency="pln",
            status="paid",
            payment_reference="AUDIT-MANUAL-1",
            payment_due_at=timezone.now(),
            access_until=timezone.now(),
        )
        self.invoice("AUDIT-MANUAL-1", manual_order=order)

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.invoice("AUDIT-MANUAL-2", manual_order=order)

    def test_missing_invoice_document_returns_404(self):
        invoice = BillingInvoice.objects.create(
            user=self.user,
            invoice_number="AUDIT-MISSING",
            issued_at=timezone.localdate(),
            document="invoices/missing.pdf",
        )
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("dashboard:customer-invoice-download", args=[invoice.pk])
        )

        self.assertEqual(response.status_code, 404)
