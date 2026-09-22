from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.accounts.models import User
from apps.dashboard.forms import BillingInvoiceForm
from .models import BillingInvoice, BillingProfile


class PrivateInvoiceTests(TestCase):
    def test_existing_private_invoice_form_does_not_request_a_public_url(self):
        from unittest.mock import patch
        invoice = BillingInvoice.objects.create(user=self.user, invoice_number="FV-2", issued_at=timezone.now().date(), document=SimpleUploadedFile("private.pdf", b"%PDF-1.4 test"))
        with patch.object(invoice.document.storage, "url", side_effect=ValueError("No public URL")):
            self.assertIn('type="file"', BillingInvoiceForm(instance=invoice).as_p())

    def setUp(self):
        self.user = User.objects.create_user(username="invoice", email="invoice@example.com", password="test-password")

    def test_invoice_snapshot_and_private_download(self):
        profile = BillingProfile.objects.create(user=self.user, company_name="Original name", tax_id="PL123", invoice_email=self.user.email)
        invoice = BillingInvoice.objects.create(user=self.user, invoice_number="FV-1", issued_at=timezone.now().date(), document=SimpleUploadedFile("invoice.pdf", b"%PDF-1.4 test"))
        profile.company_name = "Changed name"
        profile.save()
        invoice.refresh_from_db()
        self.assertEqual(invoice.billing_snapshot["company_name"], "Original name")
        self.assertFalse(invoice.sent)
        self.assertEqual(self.client.get("/media/" + invoice.document.name).status_code, 404)
        self.client.force_login(self.user)
        response = self.client.get(reverse("dashboard:customer-invoice-download", args=[invoice.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        response.close()
        other = User.objects.create_user(username="other", email="other@example.com")
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("dashboard:customer-invoice-download", args=[invoice.pk])).status_code, 404)

    def test_disguised_pdf_upload_is_rejected(self):
        form = BillingInvoiceForm(data={"invoice_number": "FV-1", "issued_at": "2026-09-06"}, files={"document": SimpleUploadedFile("invoice.pdf", b"<html>not a PDF</html>")})
        self.assertFalse(form.is_valid())
