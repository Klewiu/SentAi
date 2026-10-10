import json
import hmac
import hashlib
import time
from datetime import timedelta
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from django.urls import reverse
from django.utils.translation import override

from apps.accounts.models import AccountType, UserPlanTier
from apps.billing.models import BillingInvoice, BillingPayment, BillingPlanPrice, BillingProfile, BillingSubscription, ManualPlanOrder, ManualPlanOrderStatus
from apps.companies.models import Organization, VerificationStatus
from apps.notifications.models import AdminNotification, CustomerNotification, NotificationCategory, NotificationSeverity
from apps.sales.models import ProspectActivity, ProspectClient, SellerSettlement


User = get_user_model()


@override_settings(ADMIN_MFA_REQUIRED=False)
class BillingInvoiceTrackingTests(TestCase):
    def setUp(self):
        self.media_directory = TemporaryDirectory()
        self.media_override = override_settings(MEDIA_ROOT=self.media_directory.name)
        self.media_override.enable()
        self.admin = User.objects.create_superuser(
            username="invoice-admin",
            email="invoice-admin@example.com",
            password="strong-pass-123",
        )
        self.customer = User.objects.create_user(
            username="invoice-customer",
            email="invoice-customer@example.com",
            password="strong-pass-123",
        )
        self.payment = BillingPayment.objects.create(
            user=self.customer,
            stripe_invoice_id="in_invoice_tracking",
            amount_paid=4900,
            currency="pln",
            status="paid",
        )
        self.client.force_login(self.admin)

    def tearDown(self):
        self.media_override.disable()
        self.media_directory.cleanup()
        super().tearDown()

    def test_admin_can_save_invoice_tracking_information(self):
        response = self.client.post(
            reverse("dashboard:billing-payment-invoice-update", args=[self.payment.pk]),
            {
                "invoice_issued": "on",
                "invoice_issued_at": "2026-06-27",
                "invoice_sent": "on",
                "invoice_sent_at": "2026-06-28",
                "invoice_number": "FV/2026/001",
                "invoice_document": SimpleUploadedFile(
                    "FV-2026-001.pdf",
                    b"%PDF-1.4 test invoice",
                    content_type="application/pdf",
                ),
            },
        )

        self.assertRedirects(response, reverse("dashboard:billing-overview"))
        self.payment.refresh_from_db()
        self.assertTrue(self.payment.invoice_issued)
        self.assertEqual(self.payment.invoice_issued_at.isoformat(), "2026-06-27")
        self.assertTrue(self.payment.invoice_sent)
        self.assertEqual(self.payment.invoice_sent_at.isoformat(), "2026-06-28")
        self.assertEqual(self.payment.invoice_number, "FV/2026/001")

    def test_customer_invoice_history_shows_full_billing_details(self):
        profile = BillingProfile.objects.create(
            user=self.customer,
            company_name="Pełne Dane Sp. z o.o.",
            tax_id="PL5260250274",
            street="Fakturowa 12",
            postal_code="00-950",
            city="Warszawa",
            country="PL",
            invoice_email="faktury@pelne-dane.pl",
        )

        response = self.client.get(
            reverse("dashboard:billing-customer-invoices", args=[self.customer.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pełne Dane Sp. z o.o.")
        self.assertContains(response, "PL5260250274")
        self.assertContains(response, "Fakturowa 12")
        self.assertContains(response, "00-950 Warszawa")
        self.assertContains(response, "faktury@pelne-dane.pl")
        changed_at = timezone.localtime(profile.updated_at).strftime("%Y-%m-%d %H:%M")
        self.assertContains(response, changed_at)

    def test_admin_permanent_purge_requires_exact_confirmation_and_deletes_archive(self):
        customer_pk = self.customer.pk
        self.customer.is_active = False
        self.customer.closed_at = timezone.now()
        self.customer.closed_display_name = "Archived Customer"
        self.customer.closed_email = "invoice-customer@example.com"
        self.customer.email = f"closed-{customer_pk}@deleted.invalid"
        self.customer.username = f"closed_{customer_pk}"
        self.customer.save()
        organization = Organization.objects.create(owner=self.customer, name="Archived Organization")
        invoice = BillingInvoice.objects.create(
            user=self.customer,
            payment=self.payment,
            invoice_number="FV/PURGE/1",
            issued_at=timezone.localdate(),
            document=SimpleUploadedFile("purge.pdf", b"%PDF-1.4 purge", content_type="application/pdf"),
        )
        document_storage = invoice.document.storage
        document_name = invoice.document.name
        AdminNotification.objects.create(
            title="Archived customer notice",
            message="Contains archived customer information",
            category=NotificationCategory.CUSTOMER,
            customer=self.customer,
        )

        invalid = self.client.post(
            reverse("dashboard:client-purge", args=[customer_pk]),
            {"purge_acknowledged": "on", "confirmation": "DELETE"},
        )
        self.assertRedirects(invalid, reverse("dashboard:client-detail", args=[customer_pk]))
        self.assertTrue(User.objects.filter(pk=customer_pk).exists())

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("dashboard:client-purge", args=[customer_pk]),
                {"purge_acknowledged": "on", "confirmation": f"DELETE #{customer_pk}"},
            )

        self.assertRedirects(response, reverse("dashboard:client-list"))
        self.assertFalse(User.objects.filter(pk=customer_pk).exists())
        self.assertFalse(Organization.objects.filter(pk=organization.pk).exists())
        self.assertFalse(BillingInvoice.objects.filter(pk=invoice.pk).exists())
        self.assertFalse(AdminNotification.objects.filter(title="Archived customer notice").exists())
        self.assertFalse(document_storage.exists(document_name))

    def test_billing_overview_breaks_active_annual_turnover_down_by_plan_and_method(self):
        now = timezone.now()
        subscription = BillingSubscription.objects.create(
            user=self.customer,
            tier=UserPlanTier.BASIC,
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=365),
        )
        self.payment.subscription = subscription
        self.payment.amount_paid = 10000
        self.payment.status = "paid"
        self.payment.paid_at = now
        self.payment.save(update_fields=["subscription", "amount_paid", "status", "paid_at"])

        for number in (1, 2):
            manual_user = User.objects.create_user(
                username=f"plus-manual-{number}",
                email=f"plus-manual-{number}@example.com",
                password="strong-pass-123",
            )
            ManualPlanOrder.objects.create(
                user=manual_user,
                tier=UserPlanTier.PLUS,
                amount=40000,
                currency="pln",
                status=ManualPlanOrderStatus.PAID,
                payment_reference=f"PLUS-TURNOVER-{number}",
                payment_due_at=now,
                access_until=now + timedelta(days=365),
                paid_at=now,
            )

        response = self.client.get(reverse("dashboard:billing-overview"))

        self.assertEqual(response.status_code, 200)
        summary = {row["tier"]: row for row in response.context["plan_turnover_rows"]}
        self.assertEqual(summary[UserPlanTier.BASIC]["stripe_annual_label"], "100 PLN")
        self.assertEqual(summary[UserPlanTier.BASIC]["stripe_monthly_label"], "8.33 PLN")
        self.assertEqual(summary[UserPlanTier.BASIC]["stripe_count"], 1)
        self.assertEqual(summary[UserPlanTier.PLUS]["manual_annual_label"], "800 PLN")
        self.assertEqual(summary[UserPlanTier.PLUS]["manual_monthly_label"], "66.67 PLN")
        self.assertEqual(summary[UserPlanTier.PLUS]["manual_count"], 2)

    def test_admin_notification_center_lists_and_closes_notifications(self):
        page = self.client.get(reverse("dashboard:notifications"))

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "New customer account")
        notification = AdminNotification.objects.filter(closed_at__isnull=True).first()

        response = self.client.post(reverse("dashboard:notification-close", args=[notification.pk]))

        self.assertRedirects(response, reverse("dashboard:notifications"))
        notification.refresh_from_db()
        self.assertIsNotNone(notification.closed_at)
        self.assertEqual(notification.closed_by, self.admin)

    def test_polish_admin_notification_center_uses_polish_content_and_badges(self):
        notification = AdminNotification.objects.get(reference_key=f"user:{self.customer.pk}:created")
        AdminNotification.objects.filter(pk=notification.pk).update(title_pl="", message_pl="")

        with override("pl"):
            page = self.client.get(reverse("dashboard:notifications"))

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Nowe konto klienta")
        self.assertContains(page, "utworzył konto klienta")
        self.assertContains(page, "Klient")
        self.assertContains(page, "Informacja")
        self.assertNotContains(page, "New customer account")
        notification.refresh_from_db()
        self.assertEqual(notification.title_pl, "Nowe konto klienta")

    def test_notification_scan_creates_invoice_needed_alert(self):
        self.payment.status = "paid"
        self.payment.paid_at = timezone.now()
        self.payment.save(update_fields=["status", "paid_at", "updated_at"])

        page = self.client.get(reverse("dashboard:notifications"), {"q": self.customer.email})

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Stripe payment needs invoice")
        self.assertTrue(AdminNotification.objects.filter(reference_key=f"payment:{self.payment.pk}:invoice-needed").exists())

    def test_admin_notifications_can_filter_by_type(self):
        AdminNotification.objects.create(
            title="Manual alert",
            message="Manual alert message",
            category=NotificationCategory.MANUAL_PLAN,
            severity=NotificationSeverity.WARNING,
            customer=self.customer,
        )
        AdminNotification.objects.create(
            title="Invoice alert",
            message="Invoice alert message",
            category=NotificationCategory.INVOICE,
            severity=NotificationSeverity.WARNING,
            customer=self.customer,
        )

        response = self.client.get(reverse("dashboard:notifications"), {"show": "all", "type": NotificationCategory.INVOICE})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invoice alert")
        self.assertNotContains(response, "Manual alert")

    def test_sent_invoice_requires_date_and_number(self):
        response = self.client.post(
            reverse("dashboard:billing-payment-invoice-update", args=[self.payment.pk]),
            {"invoice_sent": "on"},
        )

        self.assertRedirects(response, reverse("dashboard:billing-overview"))
        self.payment.refresh_from_db()
        self.assertFalse(self.payment.invoice_sent)

    def test_payment_invoice_rejects_duplicate_invoice_number(self):
        BillingInvoice.objects.create(
            user=self.customer,
            issued_at="2026-06-26",
            invoice_number="FV/DUPLICATE",
            document=SimpleUploadedFile(
                "existing.pdf",
                b"%PDF-1.4 existing invoice",
                content_type="application/pdf",
            ),
        )

        response = self.client.post(
            reverse("dashboard:billing-payment-invoice-update", args=[self.payment.pk]),
            {
                "invoice_issued": "on",
                "invoice_issued_at": "2026-06-27",
                "invoice_number": "FV/DUPLICATE",
                "invoice_document": SimpleUploadedFile(
                    "duplicate.pdf",
                    b"%PDF-1.4 duplicate invoice",
                    content_type="application/pdf",
                ),
            },
        )

        self.assertRedirects(response, reverse("dashboard:billing-overview"))
        self.payment.refresh_from_db()
        self.assertFalse(self.payment.invoice_issued)
        self.assertEqual(BillingInvoice.objects.filter(invoice_number="FV/DUPLICATE").count(), 1)

    def test_customer_can_list_and_download_own_issued_invoice(self):
        invoice = BillingInvoice.objects.create(
            user=self.customer,
            issued_at="2026-06-27",
            invoice_number="FV/2026/002",
            document=SimpleUploadedFile("FV-2026-002.pdf", b"%PDF-1.4 customer invoice", content_type="application/pdf"),
        )
        self.client.force_login(self.customer)

        page = self.client.get(reverse("dashboard:customer-invoices"))
        download = self.client.get(reverse("dashboard:customer-invoice-download", args=[invoice.pk]))

        self.assertContains(page, "FV/2026/002")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(b"".join(download.streaming_content), b"%PDF-1.4 customer invoice")

    def test_customer_notification_center_shows_welcome_and_invoice_notice(self):
        invoice = BillingInvoice.objects.create(
            user=self.customer,
            issued_at="2026-07-08",
            invoice_number="FV/CUSTOMER/001",
            document=SimpleUploadedFile("customer-notice.pdf", b"%PDF-1.4 customer notice", content_type="application/pdf"),
        )
        self.client.force_login(self.customer)

        response = self.client.get(reverse("dashboard:customer-notifications"), {"show": "all", "type": NotificationCategory.INVOICE})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "New invoice available")
        self.assertContains(response, invoice.invoice_number)
        self.assertTrue(CustomerNotification.objects.filter(user=self.customer, reference_key=f"customer:{self.customer.pk}:welcome").exists())

    def test_customer_notifications_use_polish_content_when_language_is_polish(self):
        self.client.force_login(self.customer)

        with override("pl"):
            response = self.client.get(reverse("dashboard:customer-notifications"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Uzupełnij dane do faktury")
        self.assertContains(response, "Wybierz plan")
        self.assertNotContains(response, "Complete billing details")
        self.assertNotContains(response, "Choose a plan")

    def test_customer_notifications_scan_renewal_reminders(self):
        BillingSubscription.objects.create(
            user=self.customer,
            tier=UserPlanTier.PRO,
            status="active",
            current_period_end=timezone.now() + timedelta(days=10),
        )
        ManualPlanOrder.objects.create(
            user=self.customer,
            amount=48000,
            currency="pln",
            status=ManualPlanOrderStatus.PAID,
            payment_reference="PRO-RENEW-customer",
            payment_due_at=timezone.now() - timedelta(days=350),
            access_until=timezone.now() + timedelta(days=10),
            paid_at=timezone.now() - timedelta(days=350),
        )
        self.client.force_login(self.customer)

        response = self.client.get(reverse("dashboard:customer-notifications"), {"show": "all"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Subscription renews soon")
        self.assertContains(response, "Pro Manual ends in 14 days")
        self.assertNotContains(response, "Pro Manual ends in 30 days")

        self.client.force_login(self.admin)
        admin_response = self.client.get(reverse("dashboard:notifications"), {"show": "all"})
        self.assertContains(admin_response, "Stripe subscription renews within 14 days")
        self.assertContains(admin_response, "Pro Manual ends within 14 days")

    def test_customer_cannot_download_another_customers_invoice(self):
        invoice = BillingInvoice.objects.create(
            user=self.customer,
            issued_at="2026-06-27",
            invoice_number="FV/PRIVATE",
            document=SimpleUploadedFile("private-invoice.pdf", b"%PDF-1.4 private", content_type="application/pdf"),
        )
        other_customer = User.objects.create_user(
            username="other-invoice-customer",
            email="other-invoice-customer@example.com",
            password="strong-pass-123",
        )
        self.client.force_login(other_customer)

        response = self.client.get(reverse("dashboard:customer-invoice-download", args=[invoice.pk]))

        self.assertEqual(response.status_code, 404)

    def test_admin_can_add_invoice_for_subscription_without_payment(self):
        subscription = BillingSubscription.objects.create(
            user=self.customer,
            tier=UserPlanTier.PRO,
            status="active",
            current_period_end=timezone.now() + timedelta(days=365),
        )
        BillingPayment.objects.create(
            user=self.customer,
            subscription=subscription,
            stripe_invoice_id="in_paid_subscription_invoice",
            amount_paid=40000,
            currency="pln",
            status="paid",
            paid_at=timezone.now(),
        )

        overview = self.client.get(reverse("dashboard:billing-overview"))
        self.assertContains(overview, "No invoice")

        response = self.client.post(
            reverse("dashboard:billing-invoice-add", args=[subscription.pk]),
            {
                "issued_at": "2026-06-28",
                "invoice_number": "FV/SUB/001",
                "document": SimpleUploadedFile("subscription.pdf", b"%PDF-1.4 subscription", content_type="application/pdf"),
                "sent": "on",
                "sent_at": "2026-06-28",
            },
        )

        self.assertRedirects(response, reverse("dashboard:billing-overview"))
        invoice = BillingInvoice.objects.get(invoice_number="FV/SUB/001")
        self.assertEqual(invoice.user, self.customer)
        self.assertEqual(invoice.subscription, subscription)
        self.assertTrue(invoice.sent)
        self.assertEqual(invoice.sent_at.isoformat(), "2026-06-28")

    def test_billing_overview_and_invoice_admin_accept_sorting_and_search(self):
        now = timezone.now()
        profile = BillingProfile.objects.create(
            user=self.customer,
            company_name="Invoice Customer Sp. z o.o.",
            tax_id="PL5260250274",
            street="Fakturowa 10",
            postal_code="00-001",
            city="Warszawa",
            country="PL",
            invoice_email="faktury@example.com",
        )
        subscription = BillingSubscription.objects.create(
            user=self.customer,
            tier=UserPlanTier.PRO,
            status="active",
            current_period_end=now + timedelta(days=365),
        )
        BillingPayment.objects.create(
            user=self.customer,
            subscription=subscription,
            stripe_invoice_id="in_sortable_subscription",
            amount_paid=40000,
            currency="pln",
            status="paid",
            paid_at=now,
        )
        ManualPlanOrder.objects.create(
            user=self.customer,
            amount=48000,
            currency="pln",
            status=ManualPlanOrderStatus.PAID,
            payment_reference="PRO-SORT-client",
            payment_due_at=now + timedelta(days=14),
            access_until=now + timedelta(days=365),
            paid_at=now,
        )

        overview = self.client.get(
            reverse("dashboard:billing-overview"),
            {"manual_sort": "customer", "subscription_sort": "-payment", "q": "PRO-SORT"},
        )
        invoices = self.client.get(reverse("dashboard:billing-invoices-admin"), {"sort": "-invoice", "q": self.customer.email})

        self.assertEqual(overview.status_code, 200)
        self.assertContains(overview, "manual_sort")
        self.assertContains(overview, "subscription_sort")
        self.assertContains(overview, "PRO-SORT")
        self.assertEqual(invoices.status_code, 200)
        self.assertContains(invoices, "sort")
        self.assertContains(invoices, self.customer.email)
        self.assertContains(invoices, "Invoice Customer Sp. z o.o.")
        self.assertContains(invoices, "PL5260250274")
        self.assertContains(invoices, "Fakturowa 10, 00-001 Warszawa, PL")
        self.assertContains(invoices, "faktury@example.com")
        changed_at = timezone.localtime(profile.updated_at).strftime("%Y-%m-%d %H:%M")
        self.assertContains(invoices, changed_at)

        company_search = self.client.get(
            reverse("dashboard:billing-invoices-admin"),
            {"q": "Invoice Customer"},
        )
        self.assertContains(company_search, self.customer.email)


class DashboardPlanLimitTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="client",
            email="client@example.com",
            password="strong-pass-123",
        )
        self.other_user = User.objects.create_user(
            username="other-client",
            email="other-client@example.com",
            password="strong-pass-123",
        )
        self.client.force_login(self.user)

    def prime_purchase(self, tier, method="stripe", *, terms=True, currency="pln", upgrade=False):
        session = self.client.session
        session["pending_plan_purchase"] = {
            "tier": tier,
            "currency": currency,
            "payment_method": method,
            "terms_accepted": terms,
            "upgrade": upgrade,
        }
        session.save()

    def confirmed_checkout(self, tier, currency="pln", *, upgrade=False):
        self.prime_purchase(tier, currency=currency, upgrade=upgrade)
        url = reverse("dashboard:plan-update") + ("?upgrade=1" if upgrade else "")
        return self.client.post(url, {
            "plan_tier": tier,
            "billing_currency": currency,
            "checkout_confirmed": "1",
        })

    def post_signed_event(self, payload):
        payload["id"] = "evt_test_" + payload["type"].replace(".", "_")
        encoded = json.dumps(payload)
        timestamp = str(int(time.time()))
        signature = hmac.new(b"whsec_test", f"{timestamp}.{encoded}".encode(), hashlib.sha256).hexdigest()
        with patch("stripe.Subscription.retrieve", return_value=payload["data"]["object"]), patch("stripe.Invoice.retrieve", return_value=payload["data"]["object"]):
            return self.client.post(reverse("stripe-webhook"), data=encoded, content_type="application/json", HTTP_STRIPE_SIGNATURE=f"t={timestamp},v1={signature}")

    def create_billing_profile(self, user=None, country="PL"):
        user = user or self.user
        return BillingProfile.objects.create(
            user=user,
            customer_type="company",
            company_name="Client Company",
            tax_id="PL5260250274",
            street="Test Street 1",
            postal_code="00-001",
            city="Warsaw",
            country=country,
            invoice_email=user.email,
        )

    def test_add_company_button_visible_when_under_limit(self):
        self.user.plan_selected_at = timezone.now()
        self.user.save()
        response = self.client.get(reverse("dashboard:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("dashboard:organization-create"))

    def test_inactive_plan_notice_does_not_show_hard_coded_price(self):
        with override("pl"):
            polish = self.client.get(reverse("dashboard:home"))
        self.assertContains(polish, "Publikacja profili wymaga aktywnego płatnego planu")
        self.assertContains(polish, "Wybierz wśród 3 dostępnych planów")
        self.assertContains(polish, "Rozliczaj się poprzez cykliczną subskrypcję lub zapłać przelewem za rok")
        self.assertNotContains(polish, "100 PLN")
        self.assertNotContains(polish, "25 EUR")

        with override("en"):
            english = self.client.get(reverse("dashboard:home"))
        self.assertContains(english, "Choose from 3 available plans")
        self.assertNotContains(english, "100 PLN")
        self.assertNotContains(english, "25 EUR")

    def test_empty_dashboard_shows_one_large_add_company_button(self):
        self.user.plan_selected_at = timezone.now()
        self.user.save(update_fields=["plan_selected_at"])
        response = self.client.get(reverse("dashboard:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "+ Add company page", count=1)
        self.assertNotContains(response, "Add another company page")

    def test_dashboard_labels_add_button_as_another_page_after_first_page(self):
        self.user.plan_tier = UserPlanTier.PLUS
        self.user.plan_selected_at = timezone.now()
        self.user.save(update_fields=["plan_tier", "plan_selected_at"])
        BillingSubscription.objects.create(
            user=self.user,
            tier=UserPlanTier.PLUS,
            status="active",
            current_period_end=timezone.now() + timedelta(days=365),
        )
        Organization.objects.create(owner=self.user, name="First company", slug="first-company")

        response = self.client.get(reverse("dashboard:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Add another company page")
        self.assertNotContains(response, "+ Add company page")

    def test_verified_published_company_shows_active_service_badge(self):
        self.user.plan_selected_at = timezone.now()
        self.user.save(update_fields=["plan_selected_at"])
        BillingSubscription.objects.create(
            user=self.user,
            tier=UserPlanTier.BASIC,
            status="active",
            current_period_end=timezone.now() + timedelta(days=365),
        )
        organization = Organization.objects.create(owner=self.user, name="Verified company")
        Organization.objects.filter(pk=organization.pk).update(
            verification_status=VerificationStatus.HUMAN_ADMIN_VERIFIED
        )

        response = self.client.get(reverse("dashboard:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Verified company")
        self.assertContains(response, "SERVICE ACTIVE")

    def test_add_company_button_hidden_when_basic_limit_reached(self):
        Organization.objects.create(
            owner=self.user,
            name="Basic company",
            slug="basic-company",
        )

        response = self.client.get(reverse("dashboard:home"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse("dashboard:organization-create"))

    def test_create_view_redirects_when_basic_limit_reached(self):
        Organization.objects.create(
            owner=self.user,
            name="Basic company",
            slug="basic-company",
        )

        response = self.client.get(reverse("dashboard:organization-create"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:home"))

    def test_navbar_shows_plan_user_and_counter(self):
        response = self.client.get(reverse("dashboard:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("dashboard:plan-update"))
        self.assertContains(response, "PLANS")
        self.assertContains(response, reverse("accounts:profile"))
        self.assertContains(response, "0/1")

    @override_settings(
        STRIPE_SECRET_KEY="sk_test_dummy",
        SITE_BASE_URL="http://testserver",
        STRIPE_PLUS_PRICE_AMOUNT=4900,
        STRIPE_PRO_PRICE_AMOUNT=9900,
        STRIPE_CURRENCY="pln",
        STRIPE_PLUS_PRICE_ID="price_plus_test",
        STRIPE_PRO_PRICE_ID="price_pro_test",
    )
    @patch("apps.dashboard.views.stripe.checkout.Session.create")
    def test_user_selecting_plus_starts_stripe_checkout(self, mock_checkout_create):
        BillingPlanPrice.objects.create(tier="PLUS", currency="pln", amount=4900, stripe_price_id="price_plus_test")
        self.create_billing_profile(country="PL")
        mock_checkout_create.return_value = SimpleNamespace(url="https://checkout.stripe.test/session")

        response = self.confirmed_checkout(UserPlanTier.PLUS)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "https://checkout.stripe.test/session")
        _, kwargs = mock_checkout_create.call_args
        self.assertEqual(kwargs["mode"], "subscription")
        self.assertEqual(kwargs["line_items"][0]["price"], "price_plus_test")
        self.assertEqual(kwargs["metadata"]["billing_country"], "PL")
        self.assertEqual(kwargs["metadata"]["billing_currency"], "pln")
        self.user.refresh_from_db()
        self.assertEqual(self.user.plan_tier, UserPlanTier.BASIC)

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy", STRIPE_PLUS_PRICE_ID="price_plus_test")
    @patch("apps.dashboard.views.stripe.checkout.Session.create")
    def test_user_selecting_paid_plan_starts_the_staged_purchase_flow(self, mock_checkout_create):
        response = self.client.post(
            reverse("dashboard:plan-update"),
            {"plan_tier": UserPlanTier.PLUS},
        )

        self.assertRedirects(response, reverse("dashboard:plan-payment-method"))
        mock_checkout_create.assert_not_called()
        self.user.refresh_from_db()
        self.assertEqual(self.user.plan_tier, UserPlanTier.BASIC)

    def test_plan_page_has_three_plans_and_payment_method_is_a_separate_step(self):
        for tier, amount in ((UserPlanTier.BASIC, 10000), (UserPlanTier.PLUS, 20000), (UserPlanTier.PRO, 40000)):
            BillingPlanPrice.objects.create(tier=tier, currency="pln", amount=amount, stripe_price_id=f"price_{tier.lower()}_flow")
        page = self.client.get(reverse("dashboard:plan-update"))
        self.assertContains(page, "Basic")
        self.assertContains(page, "Plus")
        self.assertContains(page, "Pro")
        self.assertNotContains(page, "Pro Manual")
        selection = self.client.post(reverse("dashboard:plan-update"), {"plan_tier": UserPlanTier.BASIC})
        self.assertRedirects(selection, reverse("dashboard:plan-payment-method"))
        methods = self.client.get(reverse("dashboard:plan-payment-method"))
        self.assertContains(methods, "Stripe subscription")
        self.assertContains(methods, "Bank transfer")

    def test_cancelled_stripe_checkout_resets_purchase_but_keeps_billing_profile(self):
        profile = self.create_billing_profile()
        self.prime_purchase(UserPlanTier.PLUS)
        response = self.client.get(reverse("dashboard:plan-checkout-cancel"))
        self.assertRedirects(response, reverse("dashboard:plan-update"))
        self.assertNotIn("pending_plan_purchase", self.client.session)
        self.assertTrue(BillingProfile.objects.filter(pk=profile.pk).exists())

    @override_settings(STRIPE_SECRET_KEY="")
    def test_user_can_open_subscription_management_page(self):
        BillingSubscription.objects.create(
            user=self.user,
            tier=UserPlanTier.PLUS,
            stripe_customer_id="cus_test_123",
            stripe_subscription_id="sub_test_123",
            status="active",
            current_period_end=timezone.now() + timedelta(days=365),
        )

        response = self.client.get(reverse("dashboard:billing-portal"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Manage plan")
        self.assertContains(response, "PLUS")

    @override_settings(STRIPE_SECRET_KEY="")
    def test_subscription_management_explains_upgrade_and_renewal_in_both_languages(self):
        BillingSubscription.objects.create(
            user=self.user,
            tier=UserPlanTier.BASIC,
            stripe_customer_id="cus_copy_test",
            stripe_subscription_id="sub_copy_test",
            status="active",
            current_period_end=timezone.now() + timedelta(days=365),
        )

        with override("pl"):
            polish = self.client.get(reverse("dashboard:billing-portal"))
        self.assertContains(polish, "Zarządzaj planem")
        self.assertContains(polish, "Jak zmienić plan?")
        self.assertContains(polish, "Po zakończeniu okresu")
        self.assertContains(polish, "Wyłącz odnawianie")
        self.assertContains(polish, "Usuń plan")
        self.assertContains(polish, "Niewykorzystany okres planu nie podlega zwrotowi ani przeniesieniu na nowy plan")
        self.assertContains(polish, "Potwierdzam trwałe zakończenie planu")
        self.assertContains(polish, 'id="remove-plan-countdown">5</span>s')
        self.assertContains(polish, 'name="termination_acknowledged"')
        self.assertNotContains(polish, "Nie ogranicza to praw wynikających")
        self.assertContains(polish, "aktywna")
        self.assertNotContains(polish, reverse("dashboard:plan-update") + "?upgrade=1")

        with override("en"):
            english = self.client.get(reverse("dashboard:billing-portal"))
        self.assertContains(english, "How do I change my plan?")
        self.assertContains(english, "After the period ends")
        self.assertContains(english, "Turn off renewal")
        self.assertContains(english, "Remove plan")
        self.assertContains(english, "The unused plan period is not refundable")

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    @patch("apps.dashboard.views.stripe.Subscription.modify")
    def test_user_can_turn_off_renewal_without_stopping_current_access(self, mock_modify):
        subscription = BillingSubscription.objects.create(
            user=self.user,
            tier=UserPlanTier.PLUS,
            stripe_customer_id="cus_renewal_off",
            stripe_subscription_id="sub_renewal_off",
            status="active",
            current_period_end=timezone.now() + timedelta(days=180),
        )
        self.user.plan_tier = UserPlanTier.PLUS
        self.user.plan_access_status = "ACTIVE"
        self.user.save(update_fields=["plan_tier", "plan_access_status"])

        response = self.client.post(reverse("dashboard:billing-subscription-cancel-renewal"))

        self.assertRedirects(response, reverse("dashboard:billing-portal"))
        mock_modify.assert_called_once_with("sub_renewal_off", cancel_at_period_end=True)
        subscription.refresh_from_db()
        self.user.refresh_from_db()
        self.assertTrue(subscription.cancel_at_period_end)
        self.assertEqual(subscription.status, "active")
        self.assertEqual(self.user.plan_access_status, "ACTIVE")

    def test_customer_can_order_manual_pro_and_get_immediate_access(self):
        BillingPlanPrice.objects.create(
            tier=UserPlanTier.PRO,
            currency="pln",
            amount=40000,
            stripe_price_id="price_pro_manual_source",
        )
        self.create_billing_profile(country="PL")

        self.prime_purchase(UserPlanTier.PRO, method="manual")
        response = self.client.get(reverse("dashboard:manual-plan-confirm"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ManualPlanOrder.objects.filter(user=self.user).exists())

        confirmation_page = self.client.get(reverse("dashboard:manual-plan-confirm"))
        self.assertContains(confirmation_page, "325.20 PLN")
        self.assertNotContains(confirmation_page, "<strong>325.20 PLN</strong>", html=True)
        self.assertContains(confirmation_page, "400 PLN")
        pending_reference = self.client.session["pending_plan_purchase"]["payment_reference"]
        self.assertContains(confirmation_page, pending_reference)

        confirmation = self.client.post(reverse("dashboard:manual-plan-confirm"))

        self.assertRedirects(confirmation, reverse("dashboard:billing-portal"))
        self.user.refresh_from_db()
        order = ManualPlanOrder.objects.get(user=self.user)
        self.assertEqual(self.user.plan_tier, UserPlanTier.PRO)
        self.assertEqual(order.amount, 40000)
        self.assertEqual(order.currency, "pln")
        self.assertEqual(order.status, ManualPlanOrderStatus.AWAITING_PAYMENT)
        self.assertEqual(order.payment_reference, pending_reference)
        self.assertGreaterEqual((order.payment_due_at - order.created_at).days, 11)
        self.assertLessEqual((order.payment_due_at - order.created_at).days, 12)
        self.assertGreaterEqual((order.access_until - order.created_at).days, 364)
        self.assertIn("Client-Company", order.payment_reference)

        portal = self.client.get(reverse("dashboard:billing-portal"))
        self.assertContains(portal, "PRO MANUAL")
        self.assertContains(portal, "Temporary access until")
        self.assertContains(portal, "Awaiting payment")
        self.assertContains(portal, order.payment_reference)

    def test_manual_plan_terms_then_billing_then_order_confirmation(self):
        BillingPlanPrice.objects.create(
            tier=UserPlanTier.PRO,
            currency="pln",
            amount=40000,
            stripe_price_id="price_pro_manual_flow",
        )
        selection = self.client.post(
            reverse("dashboard:plan-update"),
            {"plan_tier": UserPlanTier.PRO, "billing_currency": "pln"},
        )
        self.assertRedirects(selection, reverse("dashboard:plan-payment-method"))
        method = self.client.post(reverse("dashboard:plan-payment-method"), {"payment_method": "manual"})
        billing_url = (
            f"{reverse('dashboard:billing-profile')}"
            f"?next={reverse('dashboard:plan-purchase-continue')}"
        )
        self.assertRedirects(method, billing_url)
        self.assertFalse(ManualPlanOrder.objects.filter(user=self.user).exists())

        saved = self.client.post(
            billing_url,
            {
                "company_name": "Client Company",
                "tax_id": "PL5260250274",
                "street": "Test Street 1",
                "postal_code": "00-001",
                "city": "Warsaw",
                "country": "PL",
                "invoice_email": self.user.email,
                "next": reverse("dashboard:plan-purchase-continue"),
            },
        )
        self.assertEqual(saved.status_code, 302)
        self.assertEqual(saved.url, reverse("dashboard:plan-purchase-continue"))

        continuation = self.client.get(reverse("dashboard:plan-purchase-continue"))
        self.assertRedirects(continuation, reverse("dashboard:plan-purchase-terms"))
        terms = self.client.post(reverse("dashboard:plan-purchase-terms"), {"terms_accepted": "on"})
        self.assertRedirects(terms, reverse("dashboard:manual-plan-confirm"))
        confirmation = self.client.get(reverse("dashboard:manual-plan-confirm"))
        self.assertContains(confirmation, "Confirm order and payment obligation")
        self.assertFalse(ManualPlanOrder.objects.filter(user=self.user).exists())

    def test_purchase_terms_modal_uses_payment_method_specific_agreement(self):
        self.create_billing_profile(country="PL")
        BillingPlanPrice.objects.create(
            tier=UserPlanTier.PLUS,
            currency="pln",
            amount=20000,
            stripe_price_id="price_plus_terms_modal",
        )

        self.prime_purchase(UserPlanTier.PLUS, method="stripe", terms=False)
        stripe_page = self.client.get(reverse("dashboard:plan-purchase-terms"))
        self.assertContains(stripe_page, 'id="purchase-terms-modal"')
        self.assertContains(stripe_page, "renews automatically every 12 months")
        self.assertNotContains(stripe_page, "The payment is one-time and does not renew automatically")

        self.prime_purchase(UserPlanTier.PLUS, method="manual", terms=False)
        manual_page = self.client.get(reverse("dashboard:plan-purchase-terms"))
        self.assertContains(manual_page, 'id="purchase-terms-modal"')
        self.assertContains(manual_page, "The payment is one-time and does not renew automatically")
        self.assertNotContains(manual_page, "renews automatically every 12 months")

    def test_purchase_terms_checkbox_is_validated_by_server(self):
        self.create_billing_profile(country="PL")
        BillingPlanPrice.objects.create(
            tier=UserPlanTier.BASIC,
            currency="pln",
            amount=10000,
            stripe_price_id="price_basic_required_terms",
        )
        self.prime_purchase(UserPlanTier.BASIC, method="manual", terms=False)

        response = self.client.post(reverse("dashboard:plan-purchase-terms"), {})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Read and accept the Terms and Agreement to continue")
        self.assertFalse(self.client.session["pending_plan_purchase"]["terms_accepted"])

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    @patch("apps.dashboard.views.stripe.checkout.Session.create")
    def test_manual_pro_blocks_parallel_stripe_subscription(self, mock_checkout_create):
        self.create_billing_profile(country="PL")
        now = timezone.now()
        ManualPlanOrder.objects.create(
            user=self.user,
            amount=48000,
            currency="pln",
            payment_reference="PRO-LOCK-client-bez-firmy",
            payment_due_at=now + timedelta(days=14),
            access_until=now + timedelta(days=365),
        )
        self.user.plan_tier = UserPlanTier.PRO
        self.user.plan_selected_at = now
        self.user.save(update_fields=["plan_tier", "plan_selected_at"])

        response = self.client.post(
            reverse("dashboard:plan-update"),
            {"plan_tier": UserPlanTier.PLUS, "billing_currency": "pln", "subscription_terms_accepted": "on"},
        )

        self.assertRedirects(response, reverse("dashboard:billing-portal"))
        mock_checkout_create.assert_not_called()

    @override_settings(ADMIN_MFA_REQUIRED=False)
    def test_admin_can_disable_manual_pro(self):
        now = timezone.now()
        order = ManualPlanOrder.objects.create(
            user=self.user,
            amount=48000,
            currency="pln",
            payment_reference="PRO-DISABLE-client-bez-firmy",
            payment_due_at=now + timedelta(days=14),
            access_until=now + timedelta(days=365),
        )
        self.user.plan_tier = UserPlanTier.PRO
        self.user.save(update_fields=["plan_tier"])
        admin = User.objects.create_superuser("manual-admin", "manual-admin@example.com", "strong-pass-123")
        self.client.force_login(admin)

        response = self.client.post(reverse("dashboard:manual-plan-disable", args=[order.pk]))

        self.assertRedirects(response, reverse("dashboard:billing-overview"))
        self.user.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(self.user.plan_tier, UserPlanTier.PRO)
        self.assertEqual(self.user.plan_access_status, "EXPIRED")
        from apps.billing.access import has_publication_access
        self.assertFalse(has_publication_access(self.user))
        self.assertEqual(order.status, ManualPlanOrderStatus.DISABLED)
        self.assertEqual(order.disabled_by, admin)

    @override_settings(ADMIN_MFA_REQUIRED=False)
    def test_admin_can_restore_late_manual_payment_without_restarting_plan_year(self):
        now = timezone.now()
        original_access_until = now + timedelta(days=240)
        admin = User.objects.create_superuser("restore-admin", "restore-admin@example.com", "strong-pass-123")
        order = ManualPlanOrder.objects.create(
            user=self.user,
            amount=48000,
            currency="pln",
            payment_reference="PRO-LATE-client-bez-firmy",
            payment_due_at=now - timedelta(days=1),
            access_until=original_access_until,
            status=ManualPlanOrderStatus.DISABLED,
            disabled_at=now,
            disabled_by=admin,
        )
        self.user.plan_tier = UserPlanTier.PRO
        self.user.plan_access_status = "EXPIRED"
        self.user.save(update_fields=["plan_tier", "plan_access_status"])
        self.client.force_login(admin)

        overview = self.client.get(reverse("dashboard:billing-overview"))
        self.assertContains(overview, reverse("dashboard:manual-plan-mark-paid", args=[order.pk]))
        self.assertContains(overview, "The annual period will not restart")

        response = self.client.post(reverse("dashboard:manual-plan-mark-paid", args=[order.pk]))

        self.assertRedirects(response, reverse("dashboard:billing-overview"))
        order.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(order.status, ManualPlanOrderStatus.PAID)
        self.assertEqual(order.access_until, original_access_until)
        self.assertIsNotNone(order.paid_at)
        self.assertIsNone(order.disabled_at)
        self.assertIsNone(order.disabled_by)
        self.assertEqual(self.user.plan_tier, UserPlanTier.PRO)
        self.assertEqual(self.user.plan_access_status, "ACTIVE")
        from apps.billing.access import has_publication_access
        self.assertTrue(has_publication_access(self.user))

    @override_settings(ADMIN_MFA_REQUIRED=False)
    def test_restoring_previously_paid_manual_order_preserves_original_payment_time(self):
        now = timezone.now()
        original_paid_at = now - timedelta(days=20)
        original_access_until = now + timedelta(days=300)
        admin = User.objects.create_superuser("paid-restore-admin", "paid-restore@example.com", "strong-pass-123")
        order = ManualPlanOrder.objects.create(
            user=self.user,
            amount=48000,
            currency="pln",
            payment_reference="PRO-RESTORE-PAID",
            payment_due_at=now - timedelta(days=30),
            access_until=original_access_until,
            status=ManualPlanOrderStatus.DISABLED,
            paid_at=original_paid_at,
            disabled_at=now,
            disabled_by=admin,
        )
        self.client.force_login(admin)

        self.client.post(reverse("dashboard:manual-plan-mark-paid", args=[order.pk]))

        order.refresh_from_db()
        self.assertEqual(order.status, ManualPlanOrderStatus.PAID)
        self.assertEqual(order.paid_at, original_paid_at)
        self.assertEqual(order.access_until, original_access_until)

    @override_settings(ADMIN_MFA_REQUIRED=False)
    def test_late_manual_payment_after_original_year_does_not_restore_access(self):
        now = timezone.now()
        original_access_until = now - timedelta(days=1)
        admin = User.objects.create_superuser("expired-restore-admin", "expired-restore@example.com", "strong-pass-123")
        order = ManualPlanOrder.objects.create(
            user=self.user,
            amount=48000,
            currency="pln",
            payment_reference="PRO-EXPIRED-LATE",
            payment_due_at=now - timedelta(days=355),
            access_until=original_access_until,
            status=ManualPlanOrderStatus.DISABLED,
            disabled_at=now - timedelta(days=340),
            disabled_by=admin,
        )
        self.user.plan_tier = UserPlanTier.PRO
        self.user.plan_access_status = "EXPIRED"
        self.user.save(update_fields=["plan_tier", "plan_access_status"])
        self.client.force_login(admin)

        self.client.post(reverse("dashboard:manual-plan-mark-paid", args=[order.pk]))

        order.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(order.status, ManualPlanOrderStatus.PAID)
        self.assertEqual(order.access_until, original_access_until)
        self.assertEqual(self.user.plan_access_status, "EXPIRED")
        from apps.billing.access import has_publication_access
        self.assertFalse(has_publication_access(self.user))

    @override_settings(ADMIN_MFA_REQUIRED=False)
    def test_confirmed_manual_payment_counts_as_turnover_and_accepts_invoice(self):
        now = timezone.now()
        order = ManualPlanOrder.objects.create(
            user=self.user,
            amount=48000,
            currency="pln",
            payment_reference="PRO-PAID-client-bez-firmy",
            payment_due_at=now + timedelta(days=14),
            access_until=now + timedelta(days=365),
        )
        original_access_until = order.access_until
        admin = User.objects.create_superuser("turnover-admin", "turnover-admin@example.com", "strong-pass-123")
        self.client.force_login(admin)

        confirmation = self.client.post(reverse("dashboard:manual-plan-mark-paid", args=[order.pk]))

        self.assertRedirects(confirmation, reverse("dashboard:billing-overview"))
        order.refresh_from_db()
        self.assertEqual(order.status, ManualPlanOrderStatus.PAID)
        self.assertEqual(order.access_until, original_access_until)
        self.client.force_login(self.user)
        portal = self.client.get(reverse("dashboard:billing-portal"))
        self.assertContains(portal, "PRO MANUAL")
        self.assertContains(portal, "Paid")
        displayed_access_until = timezone.localtime(order.access_until).strftime("%Y-%m-%d %H:%M")
        self.assertContains(portal, displayed_access_until)
        self.assertContains(portal, "The annual period is counted from activation")
        self.client.force_login(admin)
        overview = self.client.get(reverse("dashboard:billing-overview"))
        self.assertContains(overview, "480 PLN")
        self.assertContains(overview, "40 PLN")

        invoice_response = self.client.post(
            reverse("dashboard:manual-plan-invoice", args=[order.pk]),
            {
                "issued_at": "2026-07-06",
                "sent_at": "2026-07-07",
                "invoice_number": "FV/MANUAL/001",
                "document": SimpleUploadedFile("manual.pdf", b"%PDF-1.4 manual", content_type="application/pdf"),
            },
        )

        self.assertRedirects(invoice_response, reverse("dashboard:billing-invoices-admin"))
        invoice = BillingInvoice.objects.get(manual_order=order)
        self.assertEqual(invoice.user, self.user)
        self.assertEqual(invoice.invoice_number, "FV/MANUAL/001")
        self.assertEqual(invoice.sent_at.isoformat(), "2026-07-07")

    def test_each_paid_stripe_payment_gets_separate_invoice_task(self):
        subscription = BillingSubscription.objects.create(
            user=self.user,
            tier=UserPlanTier.PRO,
            status="active",
            current_period_end=timezone.now() + timedelta(days=365),
            stripe_subscription_id="sub_invoice_tasks",
        )
        first = BillingPayment.objects.create(
            user=self.user,
            subscription=subscription,
            stripe_invoice_id="in_year_1",
            amount_paid=40000,
            currency="pln",
            status="paid",
            paid_at=timezone.now() - timedelta(days=365),
        )
        second = BillingPayment.objects.create(
            user=self.user,
            subscription=subscription,
            stripe_invoice_id="in_year_2",
            billing_reason="subscription_cycle",
            amount_paid=40000,
            currency="pln",
            status="paid",
            paid_at=timezone.now(),
        )
        BillingInvoice.objects.create(
            user=self.user,
            subscription=subscription,
            payment=first,
            issued_at="2025-07-06",
            sent_at="2025-07-07",
            invoice_number="FV/STRIPE/001",
            document=SimpleUploadedFile("stripe-year-1.pdf", b"%PDF-1.4 stripe year 1", content_type="application/pdf"),
        )
        admin = User.objects.create_superuser("invoice-task-admin", "invoice-task-admin@example.com", "strong-pass-123")
        self.client.force_login(admin)

        page = self.client.get(reverse("dashboard:billing-invoices-admin"))

        self.assertContains(page, "Upload invoice", count=1)
        self.assertContains(page, "Next period / renewal")
        response = self.client.post(
            reverse("dashboard:stripe-payment-invoice", args=[second.pk]),
            {
                "issued_at": "2026-07-06",
                "sent_at": "2026-07-08",
                "invoice_number": "FV/STRIPE/002",
                "document": SimpleUploadedFile("stripe.pdf", b"%PDF-1.4 stripe", content_type="application/pdf"),
            },
        )
        self.assertRedirects(response, reverse("dashboard:billing-invoices-admin"))
        self.assertTrue(BillingInvoice.objects.filter(payment=second, invoice_number="FV/STRIPE/002").exists())
        self.assertTrue(BillingInvoice.objects.filter(payment=first, invoice_number="FV/STRIPE/001").exists())
        detail = self.client.get(reverse("dashboard:billing-customer-invoices", args=[self.user.pk]))
        self.assertContains(detail, "FV/STRIPE/002")
        self.assertContains(detail, "2026-07-08")

    def test_each_paid_manual_renewal_gets_a_new_invoice_task(self):
        now = timezone.now()
        first = ManualPlanOrder.objects.create(
            user=self.user,
            tier=UserPlanTier.PLUS,
            amount=20000,
            currency="pln",
            status=ManualPlanOrderStatus.PAID,
            payment_reference="PLUS-MANUAL-YEAR-1",
            payment_due_at=now - timedelta(days=370),
            access_until=now - timedelta(days=5),
            paid_at=now - timedelta(days=370),
        )
        second = ManualPlanOrder.objects.create(
            user=self.user,
            tier=UserPlanTier.PLUS,
            amount=20000,
            currency="pln",
            status=ManualPlanOrderStatus.PAID,
            payment_reference="PLUS-MANUAL-YEAR-2",
            payment_due_at=now,
            access_until=now + timedelta(days=365),
            paid_at=now,
        )
        BillingInvoice.objects.create(
            user=self.user,
            manual_order=first,
            issued_at="2025-07-06",
            sent_at="2025-07-07",
            invoice_number="FV/MANUAL/YEAR/001",
            document=SimpleUploadedFile("manual-year-1.pdf", b"%PDF-1.4 manual year 1", content_type="application/pdf"),
        )
        admin = User.objects.create_superuser("manual-renewal-admin", "manual-renewal-admin@example.com", "strong-pass-123")
        self.client.force_login(admin)

        invoices = self.client.get(reverse("dashboard:billing-invoices-admin"))
        overview = self.client.get(reverse("dashboard:billing-overview"))

        self.assertContains(invoices, "Upload invoice", count=1)
        self.assertContains(invoices, "Next period / renewal")
        self.assertContains(overview, "Renewal")
        self.assertFalse(BillingInvoice.objects.filter(manual_order=second).exists())

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    @patch("apps.dashboard.views.stripe.billing_portal.Session.create")
    def test_customer_can_open_stripe_portal_to_update_payment_method(self, mock_portal_create):
        BillingSubscription.objects.create(
            user=self.user,
            tier=UserPlanTier.PLUS,
            stripe_customer_id="cus_test_123",
            stripe_subscription_id="sub_test_123",
            status="past_due",
        )
        mock_portal_create.return_value = SimpleNamespace(url="https://billing.stripe.test/session")

        response = self.client.get(reverse("dashboard:stripe-customer-portal"))

        self.assertRedirects(response, "https://billing.stripe.test/session", fetch_redirect_response=False)
        mock_portal_create.assert_called_once()
        self.assertEqual(mock_portal_create.call_args.kwargs["customer"], "cus_test_123")

    @override_settings(STRIPE_SECRET_KEY="")
    def test_past_due_subscription_shows_failed_payment_recovery(self):
        subscription = BillingSubscription.objects.create(
            user=self.user,
            tier=UserPlanTier.PLUS,
            stripe_customer_id="cus_test_123",
            stripe_subscription_id="sub_test_123",
            status="past_due",
        )
        BillingPayment.objects.create(
            user=self.user,
            subscription=subscription,
            stripe_invoice_id="in_failed_123",
            status="open",
            hosted_invoice_url="https://invoice.stripe.test/in_failed_123",
        )

        response = self.client.get(reverse("dashboard:billing-portal"))

        self.assertContains(response, "Your renewal payment failed")
        self.assertContains(response, reverse("dashboard:stripe-customer-portal"))
        self.assertContains(response, "https://invoice.stripe.test/in_failed_123")

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    @patch("apps.dashboard.views.stripe.Subscription.retrieve")
    def test_subscription_management_page_refreshes_period_dates_from_stripe(self, mock_subscription_retrieve):
        price = BillingPlanPrice.objects.create(
            tier=UserPlanTier.PLUS,
            stripe_price_id="price_plus_pln",
            amount=20000,
            currency="pln",
            active_for_new_customers=True,
        )
        BillingSubscription.objects.create(
            user=self.user,
            tier=UserPlanTier.PLUS,
            plan_price=price,
            stripe_customer_id="cus_test_123",
            stripe_subscription_id="sub_test_123",
            status="active",
            current_period_end=timezone.now() + timedelta(days=365),
        )
        mock_subscription_retrieve.return_value = {
            "id": "sub_test_123",
            "customer": "cus_test_123",
            "status": "active",
            "cancel_at_period_end": False,
            "metadata": {"user_id": str(self.user.pk), "plan_tier": UserPlanTier.PLUS},
            "items": {
                "data": [
                    {
                        "current_period_start": 1767225600,
                        "current_period_end": 1798761600,
                        "price": {"id": price.stripe_price_id},
                    }
                ]
            },
        }

        response = self.client.get(reverse("dashboard:billing-portal"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2026-01-01")
        self.assertContains(response, "2027-01-01")
        subscription = BillingSubscription.objects.get(user=self.user)
        self.assertIsNotNone(subscription.current_period_start)
        self.assertIsNotNone(subscription.current_period_end)

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    @patch("apps.dashboard.views.stripe.Subscription.delete")
    def test_user_can_terminate_subscription_immediately(self, mock_subscription_delete):
        subscription = BillingSubscription.objects.create(
            user=self.user,
            tier=UserPlanTier.PLUS,
            stripe_customer_id="cus_test_123",
            stripe_subscription_id="sub_test_123",
            status="active",
            current_period_end=timezone.now() + timedelta(days=365),
        )

        response = self.client.post(
            reverse("dashboard:billing-subscription-cancel"),
            {"action": "change", "termination_acknowledged": "on"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:home"))
        mock_subscription_delete.assert_called_once_with("sub_test_123")
        subscription.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(subscription.status, "canceled")
        self.assertFalse(subscription.cancel_at_period_end)
        self.assertIsNotNone(subscription.canceled_at)
        self.assertIsNotNone(subscription.termination_acknowledged_at)
        self.assertEqual(self.user.plan_access_status, "EXPIRED")

        new_plan = self.client.post(
            reverse("dashboard:plan-update"),
            {"plan_tier": UserPlanTier.PRO, "billing_currency": "pln"},
        )
        self.assertRedirects(new_plan, reverse("dashboard:plan-payment-method"))

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    @patch("apps.dashboard.views.stripe.Subscription.delete")
    def test_subscription_termination_requires_acknowledgement(self, mock_subscription_delete):
        subscription = BillingSubscription.objects.create(
            user=self.user,
            tier=UserPlanTier.PLUS,
            stripe_customer_id="cus_ack_test",
            stripe_subscription_id="sub_ack_test",
            status="active",
            current_period_end=timezone.now() + timedelta(days=365),
        )

        response = self.client.post(reverse("dashboard:billing-subscription-cancel"))

        self.assertRedirects(response, reverse("dashboard:billing-portal"))
        mock_subscription_delete.assert_not_called()
        subscription.refresh_from_db()
        self.assertEqual(subscription.status, "active")

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    @patch("apps.dashboard.views.stripe.Subscription.delete")
    def test_remove_plan_returns_to_dashboard_without_starting_new_purchase(self, mock_subscription_delete):
        BillingSubscription.objects.create(
            user=self.user,
            tier=UserPlanTier.BASIC,
            stripe_customer_id="cus_remove_test",
            stripe_subscription_id="sub_remove_test",
            status="active",
            current_period_end=timezone.now() + timedelta(days=365),
        )

        response = self.client.post(
            reverse("dashboard:billing-subscription-cancel"),
            {"action": "remove", "termination_acknowledged": "on"},
            follow=True,
        )

        self.assertRedirects(response, reverse("dashboard:home"))
        mock_subscription_delete.assert_called_once_with("sub_remove_test")
        self.assertContains(response, "The Stripe subscription has ended and data publication has stopped")
        self.assertNotIn("pending_plan_purchase", self.client.session)

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    @patch("apps.dashboard.views.stripe.Subscription.delete", side_effect=RuntimeError("Stripe unavailable"))
    def test_failed_stripe_termination_keeps_access_active(self, mock_subscription_delete):
        subscription = BillingSubscription.objects.create(
            user=self.user,
            tier=UserPlanTier.PLUS,
            stripe_customer_id="cus_failure_test",
            stripe_subscription_id="sub_failure_test",
            status="active",
            current_period_end=timezone.now() + timedelta(days=365),
        )
        self.user.plan_access_status = "ACTIVE"
        self.user.save(update_fields=["plan_access_status"])

        response = self.client.post(
            reverse("dashboard:billing-subscription-cancel"),
            {"action": "change", "termination_acknowledged": "on"},
        )

        self.assertRedirects(response, reverse("dashboard:billing-portal"))
        mock_subscription_delete.assert_called_once_with("sub_failure_test")
        subscription.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(subscription.status, "active")
        self.assertEqual(self.user.plan_access_status, "ACTIVE")

    def test_user_can_terminate_manual_plan_without_restarting_its_year(self):
        original_access_until = timezone.now() + timedelta(days=240)
        order = ManualPlanOrder.objects.create(
            user=self.user,
            tier=UserPlanTier.PLUS,
            amount=20000,
            currency="pln",
            status=ManualPlanOrderStatus.PAID,
            payment_reference="PLUS-CUSTOMER-CANCEL",
            payment_due_at=timezone.now() - timedelta(days=3),
            access_until=original_access_until,
            paid_at=timezone.now() - timedelta(days=3),
        )
        self.user.plan_tier = UserPlanTier.PLUS
        self.user.plan_access_status = "ACTIVE"
        self.user.save(update_fields=["plan_tier", "plan_access_status"])

        response = self.client.post(
            reverse("dashboard:billing-subscription-cancel"),
            {"action": "change", "termination_acknowledged": "on"},
        )

        self.assertRedirects(response, reverse("dashboard:home"))
        order.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(order.status, ManualPlanOrderStatus.DISABLED)
        self.assertEqual(order.access_until, original_access_until)
        self.assertIsNotNone(order.termination_acknowledged_at)
        self.assertEqual(self.user.plan_access_status, "EXPIRED")

        self.client.force_login(User.objects.create_superuser(
            "terminated-manual-admin", "terminated-manual-admin@example.com", "strong-pass-123"
        ))
        overview = self.client.get(reverse("dashboard:billing-overview"))
        self.assertNotContains(overview, reverse("dashboard:manual-plan-mark-paid", args=[order.pk]))

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    @patch("apps.dashboard.views.stripe.Subscription.modify")
    def test_user_can_reactivate_subscription_renewal(self, mock_subscription_modify):
        subscription = BillingSubscription.objects.create(
            user=self.user,
            tier=UserPlanTier.PLUS,
            stripe_customer_id="cus_test_123",
            stripe_subscription_id="sub_test_123",
            status="active",
            current_period_end=timezone.now() + timedelta(days=365),
            cancel_at_period_end=True,
        )

        response = self.client.post(reverse("dashboard:billing-subscription-reactivate"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:billing-portal"))
        mock_subscription_modify.assert_called_once_with("sub_test_123", cancel_at_period_end=False)
        subscription.refresh_from_db()
        self.assertFalse(subscription.cancel_at_period_end)

    @patch("apps.billing.catalog.import_price")
    def test_admin_can_add_billing_plan_price(self, import_price):
        admin = User.objects.create_superuser(username="price-admin", email="price-admin@example.com", password="test-password")
        self.client.force_login(admin)
        response = self.client.post(reverse("dashboard:billing-price-management"), {
            "tier": "PLUS", "stripe_price_id": "price_plus_eur",
        })
        self.assertEqual(response.status_code, 302)
        import_price.assert_called_once_with("PLUS", "price_plus_eur", admin)

    @patch("apps.billing.catalog.import_price")
    def test_admin_price_edit_imports_replacement_without_overwriting_history(self, import_price):
        admin = User.objects.create_superuser(username="edit-admin", email="edit-admin@example.com", password="test-password")
        price = BillingPlanPrice.objects.create(tier="PLUS", currency="eur", amount=4600, stripe_price_id="price_old")
        self.client.force_login(admin)
        response = self.client.post(reverse("dashboard:billing-price-edit", args=[price.pk]), {
            "tier": "PLUS", "stripe_price_id": "price_new",
        })
        self.assertEqual(response.status_code, 302)
        import_price.assert_called_once_with("PLUS", "price_new", admin)
        price.refresh_from_db()
        self.assertEqual(price.amount, 4600)

    @patch("apps.billing.catalog.activate_price")
    def test_admin_can_activate_archived_price(self, activate):
        admin = User.objects.create_superuser(username="activate-admin", email="activate-admin@example.com", password="test-password")
        price = BillingPlanPrice.objects.create(tier="PRO", currency="pln", amount=40000, stripe_price_id="price_archived", active_for_new_customers=False)
        self.client.force_login(admin)
        response = self.client.post(reverse("dashboard:billing-price-activate", args=[price.pk]))
        self.assertEqual(response.status_code, 302)
        activate.assert_called_once_with(price)

    @override_settings(STRIPE_PLUS_PRICE_ID="", STRIPE_PRO_PRICE_ID="", STRIPE_PLUS_PRICE_AMOUNT=4900)
    def test_archived_price_does_not_show_fallback_amount_on_plan_page(self):
        BillingPlanPrice.objects.create(
            tier=UserPlanTier.PLUS,
            stripe_price_id="price_plus_archived",
            amount=20000,
            currency="pln",
            active_for_new_customers=False,
        )

        response = self.client.get(reverse("dashboard:plan-update"), {"currency": "pln"})

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "49.00 PLN")
        self.assertNotContains(response, "200.00 PLN")

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy", INTERNATIONAL_BILLING_ENABLED=True)
    @patch("apps.dashboard.views.stripe.checkout.Session.create")
    def test_user_selecting_plus_in_eur_uses_eur_stripe_price(self, mock_checkout_create):
        self.create_billing_profile(country="DE")
        BillingPlanPrice.objects.create(
            tier=UserPlanTier.PLUS,
            stripe_price_id="price_plus_eur",
            amount=1900,
            currency="eur",
            active_for_new_customers=True,
        )
        BillingPlanPrice.objects.create(
            tier=UserPlanTier.PRO,
            stripe_price_id="price_pro_eur",
            amount=3900,
            currency="eur",
            active_for_new_customers=True,
        )
        mock_checkout_create.return_value = SimpleNamespace(url="https://checkout.stripe.test/eur-session")

        response = self.confirmed_checkout(UserPlanTier.PLUS, "eur", upgrade=True)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "https://checkout.stripe.test/eur-session")
        _, kwargs = mock_checkout_create.call_args
        self.assertEqual(kwargs["line_items"][0]["price"], "price_plus_eur")
        self.assertEqual(kwargs["metadata"]["billing_country"], "DE")
        self.assertEqual(kwargs["metadata"]["billing_currency"], "eur")

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    @patch("apps.dashboard.views.stripe.checkout.Session.create")
    def test_paid_plan_requires_complete_billing_profile_before_checkout(self, mock_checkout_create):
        BillingPlanPrice.objects.create(
            tier=UserPlanTier.PLUS,
            stripe_price_id="price_plus_pln",
            amount=4900,
            currency="pln",
            active_for_new_customers=True,
        )

        selection = self.client.post(
            reverse("dashboard:plan-update"),
            {"plan_tier": UserPlanTier.PLUS, "billing_currency": "pln"},
        )
        self.assertRedirects(selection, reverse("dashboard:plan-payment-method"))
        response = self.client.post(reverse("dashboard:plan-payment-method"), {"payment_method": "stripe"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            f"{reverse('dashboard:billing-profile')}?next={reverse('dashboard:plan-purchase-continue')}",
        )
        mock_checkout_create.assert_not_called()

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    @patch("apps.dashboard.views.stripe.checkout.Session.create")
    def test_paid_plan_rejects_legacy_profile_without_pl_nip_prefix(self, mock_checkout_create):
        BillingProfile.objects.create(
            user=self.user,
            company_name="Client Company",
            tax_id="5260250274",
            street="Test Street 1",
            postal_code="00-001",
            city="Warsaw",
            country="PL",
            invoice_email=self.user.email,
        )
        BillingPlanPrice.objects.create(
            tier=UserPlanTier.PLUS,
            stripe_price_id="price_plus_requires_pl_nip",
            amount=20000,
            currency="pln",
        )

        selection = self.client.post(
            reverse("dashboard:plan-update"),
            {"plan_tier": UserPlanTier.PLUS, "billing_currency": "eur"},
        )
        self.assertRedirects(selection, reverse("dashboard:plan-payment-method"))
        response = self.client.post(reverse("dashboard:plan-payment-method"), {"payment_method": "stripe"})

        self.assertRedirects(
            response,
            f"{reverse('dashboard:billing-profile')}?next={reverse('dashboard:plan-purchase-continue')}",
        )
        mock_checkout_create.assert_not_called()

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    @patch("apps.dashboard.views.stripe.checkout.Session.create")
    def test_billing_details_continue_the_accepted_plan_to_stripe(self, mock_checkout_create):
        BillingPlanPrice.objects.create(
            tier=UserPlanTier.PLUS,
            stripe_price_id="price_plus_pln",
            amount=4900,
            currency="pln",
            active_for_new_customers=True,
        )
        mock_checkout_create.return_value = SimpleNamespace(
            id="cs_pending_plus",
            url="https://checkout.stripe.test/pending-plus",
        )

        selection = self.client.post(
            reverse("dashboard:plan-update"),
            {"plan_tier": UserPlanTier.PLUS, "billing_currency": "pln"},
        )
        billing_url = (
            f"{reverse('dashboard:billing-profile')}"
            f"?next={reverse('dashboard:plan-purchase-continue')}"
        )
        self.assertRedirects(selection, reverse("dashboard:plan-payment-method"))
        method = self.client.post(reverse("dashboard:plan-payment-method"), {"payment_method": "stripe"})
        self.assertRedirects(method, billing_url)

        saved = self.client.post(
            billing_url,
            {
                "company_name": "Client Company",
                "tax_id": "PL5260250274",
                "street": "Test Street 1",
                "postal_code": "00-001",
                "city": "Warsaw",
                "country": "PL",
                "invoice_email": self.user.email,
                "next": reverse("dashboard:plan-purchase-continue"),
            },
        )
        self.assertEqual(saved.status_code, 302)
        self.assertEqual(saved.url, reverse("dashboard:plan-purchase-continue"))

        continuation = self.client.get(reverse("dashboard:plan-purchase-continue"))
        self.assertRedirects(continuation, reverse("dashboard:plan-purchase-terms"))
        terms = self.client.post(reverse("dashboard:plan-purchase-terms"), {"terms_accepted": "on"})
        self.assertRedirects(terms, reverse("dashboard:stripe-plan-confirm"))
        checkout = self.client.post(reverse("dashboard:plan-update"), {
            "plan_tier": UserPlanTier.PLUS,
            "billing_currency": "pln",
            "checkout_confirmed": "1",
        })
        self.assertEqual(checkout.url, "https://checkout.stripe.test/pending-plus")
        mock_checkout_create.assert_called_once()

    def test_billing_profile_requires_vat_id_and_hides_person_choice(self):
        response = self.client.get(reverse("dashboard:billing-profile"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "VAT ID")
        self.assertNotContains(response, 'name="customer_type"')
        self.assertNotContains(response, 'name="first_name"')
        self.assertNotContains(response, 'name="last_name"')

        response = self.client.post(
            reverse("dashboard:billing-profile"),
            {
                "company_name": "Client Company",
                "tax_id": "",
                "street": "Test Street 1",
                "postal_code": "00-001",
                "city": "Warsaw",
                "country": "PL",
                "invoice_email": self.user.email,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Polish VAT ID (NIP) is required")

    def test_polish_billing_profile_translates_labels_hints_and_validation(self):
        with override("pl"):
            profile_url = reverse("dashboard:billing-profile")
            response = self.client.get(profile_url)

            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Uzupe&#322;nij dane przed p&#322;atno&#347;ci&#261;")
            self.assertContains(response, "Nazwa firmy")
            self.assertContains(response, "NIP")
            self.assertContains(response, "Ulica i numer")
            self.assertContains(response, "Kod pocztowy")
            self.assertContains(response, "Miejscowo")
            self.assertContains(response, "Kraj rozliczenia")
            self.assertContains(response, "Polska (PL)")
            self.assertContains(response, "E-mail do faktur")
            self.assertContains(response, "Wr&#243;&#263;")

            invalid_response = self.client.post(
                profile_url,
                {
                    "company_name": "Firma Testowa",
                    "tax_id": "",
                    "street": "Testowa 1",
                    "postal_code": "00-001",
                    "city": "Warszawa",
                    "country": "PL",
                    "invoice_email": self.user.email,
                },
            )

            self.assertEqual(invalid_response.status_code, 200)
            self.assertContains(invalid_response, "NIP jest wymagany")

    def test_billing_profile_is_saved_as_company_even_if_post_is_tampered(self):
        response = self.client.post(
            reverse("dashboard:billing-profile"),
            {
                "customer_type": "person",
                "company_name": "Client Company",
                "tax_id": "PL5260250274",
                "street": "Test Street 1",
                "postal_code": "00-001",
                "city": "Warsaw",
                "country": "PL",
                "invoice_email": self.user.email,
            },
        )

        self.assertEqual(response.status_code, 302)
        profile = self.user.billing_profile
        self.assertEqual(profile.customer_type, "company")
        self.assertEqual(profile.tax_id, "PL5260250274")

    def test_billing_profile_requires_explicit_pl_prefix(self):
        response = self.client.post(
            reverse("dashboard:billing-profile"),
            {
                "company_name": "Client Company",
                "tax_id": "526-025-02-74",
                "street": "Test Street 1",
                "postal_code": "00-001",
                "city": "Warsaw",
                "country": "PL",
                "invoice_email": self.user.email,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Polish VAT ID must start with PL")

    def test_billing_profile_rejects_foreign_country(self):
        response = self.client.post(
            reverse("dashboard:billing-profile"),
            {
                "company_name": "Foreign Company",
                "tax_id": "PL5260250274",
                "street": "Test Street 1",
                "postal_code": "00-001",
                "city": "Warsaw",
                "country": "DE",
                "invoice_email": self.user.email,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "businesses registered in Poland only")
        profile = BillingProfile.objects.get(user=self.user)
        self.assertEqual(profile.country, "PL")
        self.assertEqual(profile.company_name, "")

    def test_plan_page_exposes_pln_only(self):
        BillingPlanPrice.objects.create(
            tier=UserPlanTier.BASIC,
            stripe_price_id="price_basic_pln_only",
            amount=10000,
            currency="pln",
        )
        BillingPlanPrice.objects.create(
            tier=UserPlanTier.BASIC,
            stripe_price_id="price_basic_eur_hidden",
            amount=2500,
            currency="eur",
        )

        response = self.client.get(reverse("dashboard:plan-update"), {"currency": "eur"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "100 PLN")
        self.assertNotContains(response, "25 EUR")
        self.assertEqual(response.context["billing_currencies"], ["pln"])
        self.assertEqual(response.context["selected_billing_currency"], "pln")

    def test_billing_profile_rejects_invalid_polish_vat_id(self):
        response = self.client.post(
            reverse("dashboard:billing-profile"),
            {
                "company_name": "Client Company",
                "tax_id": "PL1234567890",
                "street": "Test Street 1",
                "postal_code": "00-001",
                "city": "Warsaw",
                "country": "PL",
                "invoice_email": self.user.email,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter a valid Polish VAT ID")

    def test_billing_profile_accepts_polish_vat_id_with_valid_checksum(self):
        response = self.client.post(
            reverse("dashboard:billing-profile"),
            {
                "company_name": "Client Company",
                "tax_id": "PL5260250995",
                "street": "Test Street 1",
                "postal_code": "00-001",
                "city": "Warsaw",
                "country": "PL",
                "invoice_email": self.user.email,
            },
        )

        self.assertRedirects(response, reverse("dashboard:plan-update"))
        self.assertEqual(self.user.billing_profile.tax_id, "PL5260250995")

    def test_billing_profile_rejects_vat_prefix_that_does_not_match_country(self):
        response = self.client.post(
            reverse("dashboard:billing-profile"),
            {
                "company_name": "Client Company",
                "tax_id": "DE123456789",
                "street": "Test Street 1",
                "postal_code": "00-001",
                "city": "Warsaw",
                "country": "PL",
                "invoice_email": self.user.email,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Polish VAT ID must start with PL")

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    @patch("apps.dashboard.views.stripe.checkout.Session.create")
    def test_polish_billing_profile_forces_pln_checkout_even_if_eur_posted(self, mock_checkout_create):
        self.create_billing_profile(country="PL")
        BillingPlanPrice.objects.create(
            tier=UserPlanTier.PLUS,
            stripe_price_id="price_plus_pln",
            amount=7900,
            currency="pln",
            active_for_new_customers=True,
        )
        BillingPlanPrice.objects.create(
            tier=UserPlanTier.PLUS,
            stripe_price_id="price_plus_eur",
            amount=1900,
            currency="eur",
            active_for_new_customers=True,
        )
        mock_checkout_create.return_value = SimpleNamespace(url="https://checkout.stripe.test/pln-session")

        response = self.confirmed_checkout(UserPlanTier.PLUS, "eur")

        self.assertEqual(response.status_code, 302)
        _, kwargs = mock_checkout_create.call_args
        self.assertEqual(kwargs["line_items"][0]["price"], "price_plus_pln")

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_test", STRIPE_SECRET_KEY="sk_test_dummy")
    def test_stripe_subscription_webhook_activates_paid_plan(self):
        price = BillingPlanPrice.objects.create(
            tier=UserPlanTier.PLUS,
            stripe_price_id="price_plus_webhook",
            amount=4900,
            currency="pln",
        )
        payload = {
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_test_123",
                    "customer": "cus_test_123",
                    "status": "active",
                    "current_period_start": 1767225600,
                    "current_period_end": 1798761600,
                    "cancel_at_period_end": False,
                    "metadata": {
                        "user_id": str(self.user.pk),
                        "plan_tier": UserPlanTier.PLUS,
                    },
                    "items": {
                        "data": [
                            {
                                "price": {
                                    "id": price.stripe_price_id,
                                }
                            }
                        ]
                    },
                }
            },
        }

        response = self.post_signed_event(payload)

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.plan_tier, UserPlanTier.PLUS)
        self.assertEqual(self.user.billing_subscription.stripe_subscription_id, "sub_test_123")

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_test", STRIPE_SECRET_KEY="sk_test_dummy")
    def test_invoice_paid_webhook_records_renewal_payment(self):
        subscription = BillingSubscription.objects.create(
            user=self.user,
            tier=UserPlanTier.PLUS,
            stripe_customer_id="cus_test_123",
            stripe_subscription_id="sub_test_123",
            status="active",
            current_period_end=timezone.now() + timedelta(days=365),
        )
        payload = {
            "type": "invoice.paid",
            "data": {
                "object": {
                    "id": "in_renewal_123",
                    "subscription": "sub_test_123",
                    "customer": "cus_test_123",
                    "payment_intent": "pi_renewal_123",
                    "amount_paid": 20000,
                    "currency": "pln",
                    "status": "paid",
                    "billing_reason": "subscription_cycle",
                    "hosted_invoice_url": "https://invoice.stripe.test/in_renewal_123",
                    "invoice_pdf": "https://invoice.stripe.test/in_renewal_123.pdf",
                    "status_transitions": {
                        "paid_at": 1798761600,
                    },
                }
            },
        }

        response = self.post_signed_event(payload)

        self.assertEqual(response.status_code, 200)
        payment = self.user.billing_payments.get(stripe_invoice_id="in_renewal_123")
        self.assertEqual(payment.subscription, subscription)
        self.assertEqual(payment.amount_paid, 20000)
        self.assertEqual(payment.currency, "pln")
        self.assertEqual(payment.status, "paid")
        self.assertEqual(payment.billing_reason, "subscription_cycle")
        subscription.refresh_from_db()
        self.assertEqual(subscription.latest_invoice_id, "in_renewal_123")
        self.assertIsNotNone(subscription.latest_payment_at)

        payload["type"] = "invoice.payment_succeeded"
        payload["data"]["object"]["id"] = "in_succeeded_123"
        payload["data"]["object"]["payment_intent"] = "pi_succeeded_123"
        response = self.post_signed_event(payload)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.user.billing_payments.filter(stripe_invoice_id="in_succeeded_123", status="paid").exists())

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_test", STRIPE_SECRET_KEY="sk_test_dummy")
    def test_unpaid_subscription_webhook_revokes_paid_access(self):
        self.user.plan_tier = UserPlanTier.PLUS
        self.user.paid_plan_started_at = timezone.now()
        self.user.save(update_fields=["plan_tier", "paid_plan_started_at"])
        BillingSubscription.objects.create(
            user=self.user,
            tier=UserPlanTier.PLUS,
            stripe_customer_id="cus_test_123",
            stripe_subscription_id="sub_test_123",
            status="past_due",
        )
        payload = {
            "type": "customer.subscription.updated",
            "data": {"object": {
                "id": "sub_test_123",
                "customer": "cus_test_123",
                "status": "unpaid",
                "metadata": {"user_id": str(self.user.pk), "plan_tier": UserPlanTier.PLUS},
                "items": {"data": []},
            }},
        }

        response = self.post_signed_event(payload)

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.plan_tier, UserPlanTier.PLUS)
        self.assertEqual(self.user.plan_access_status, "EXPIRED")

    def test_checkout_confirmation_without_staged_terms_is_rejected(self):
        response = self.client.post(
            reverse("dashboard:plan-update"),
            {"plan_tier": UserPlanTier.BASIC, "checkout_confirmed": "1"},
        )

        self.assertRedirects(response, reverse("dashboard:plan-update"))
        self.user.refresh_from_db()
        self.assertIsNone(self.user.plan_selected_at)

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    @patch("apps.dashboard.views.stripe.Subscription.modify")
    def test_paid_subscriber_selecting_basic_is_blocked_without_losing_access(self, mock_subscription_modify):
        self.user.plan_tier = UserPlanTier.PLUS
        self.user.plan_selected_at = timezone.now()
        self.user.paid_plan_started_at = timezone.now()
        self.user.save(update_fields=["plan_tier", "plan_selected_at", "paid_plan_started_at"])
        subscription = BillingSubscription.objects.create(
            user=self.user,
            tier=UserPlanTier.PLUS,
            stripe_customer_id="cus_test_123",
            stripe_subscription_id="sub_test_123",
            status="active",
            current_period_end=timezone.now() + timedelta(days=365),
        )

        response = self.client.post(
            reverse("dashboard:plan-update"),
            {"plan_tier": UserPlanTier.BASIC, "subscription_terms_accepted": True},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:billing-portal"))
        mock_subscription_modify.assert_not_called()
        self.user.refresh_from_db()
        subscription.refresh_from_db()
        self.assertEqual(self.user.plan_tier, UserPlanTier.PLUS)
        self.assertFalse(subscription.cancel_at_period_end)

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy", SITE_BASE_URL="http://testserver")
    @patch("apps.billing.services.upgrade_subscription")
    def test_plus_subscriber_selecting_pro_starts_upgrade_payment(self, mock_checkout_create):
        self.create_billing_profile(country="PL")
        plus_price = BillingPlanPrice.objects.create(
            tier=UserPlanTier.PLUS,
            stripe_price_id="price_plus_pln",
            amount=20000,
            currency="pln",
            active_for_new_customers=True,
        )
        BillingPlanPrice.objects.create(
            tier=UserPlanTier.PRO,
            stripe_price_id="price_pro_pln",
            amount=40000,
            currency="pln",
            active_for_new_customers=True,
        )
        self.user.plan_tier = UserPlanTier.PLUS
        self.user.plan_selected_at = timezone.now()
        self.user.paid_plan_started_at = timezone.now()
        self.user.save(update_fields=["plan_tier", "plan_selected_at", "paid_plan_started_at"])
        BillingSubscription.objects.create(
            user=self.user,
            tier=UserPlanTier.PLUS,
            plan_price=plus_price,
            stripe_customer_id="cus_test_123",
            stripe_subscription_id="sub_test_123",
            status="active",
            current_period_end=timezone.now() + timedelta(days=365),
        )
        mock_checkout_create.return_value = "https://invoice.stripe.test/upgrade"

        response = self.confirmed_checkout(UserPlanTier.PRO, upgrade=True)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "https://invoice.stripe.test/upgrade")
        mock_checkout_create.assert_called_once()
        args, _ = mock_checkout_create.call_args
        self.assertEqual(args[0].pk, self.user.pk)
        self.assertEqual(args[1].tier, UserPlanTier.PRO)

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    @patch("apps.dashboard.views.stripe.checkout.Session.create")
    def test_pro_subscriber_cannot_downgrade_to_plus_during_paid_period(self, mock_checkout_create):
        self.user.plan_tier = UserPlanTier.PRO
        self.user.plan_selected_at = timezone.now()
        self.user.paid_plan_started_at = timezone.now()
        self.user.save(update_fields=["plan_tier", "plan_selected_at", "paid_plan_started_at"])
        BillingSubscription.objects.create(
            user=self.user,
            tier=UserPlanTier.PRO,
            stripe_customer_id="cus_test_123",
            stripe_subscription_id="sub_test_123",
            status="active",
            current_period_end=timezone.now() + timedelta(days=365),
        )

        response = self.client.post(
            reverse("dashboard:plan-update"),
            {
                "plan_tier": UserPlanTier.PLUS,
                "billing_currency": "pln",
                "subscription_terms_accepted": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:billing-portal"))
        mock_checkout_create.assert_not_called()

    def test_active_subscriber_opening_plans_is_redirected_to_billing_portal(self):
        BillingSubscription.objects.create(
            user=self.user,
            tier=UserPlanTier.BASIC,
            stripe_customer_id="cus_test_123",
            stripe_subscription_id="sub_test_123",
            status="active",
        )

        response = self.client.get(reverse("dashboard:plan-update"))

        self.assertRedirects(response, reverse("dashboard:billing-portal"))

    def test_expired_subscriber_opening_plans_can_choose_any_plan(self):
        self.user.plan_tier = UserPlanTier.PRO
        self.user.save(update_fields=["plan_tier"])
        BillingSubscription.objects.create(
            user=self.user,
            tier=UserPlanTier.PRO,
            stripe_customer_id="cus_expired",
            stripe_subscription_id="sub_expired",
            status="canceled",
            current_period_end=timezone.now() - timedelta(seconds=1),
        )

        response = self.client.get(reverse("dashboard:plan-update"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="BASIC"')
        self.assertContains(response, 'value="PLUS"')
        self.assertContains(response, 'value="PRO"')

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    @patch("apps.dashboard.views.stripe.checkout.Session.create")
    def test_expired_subscriber_can_buy_again_with_saved_account_data(self, checkout_create):
        self.create_billing_profile(country="PL")
        company = Organization.objects.create(owner=self.user, name="Saved company", slug="saved-company")
        self.user.plan_tier = UserPlanTier.PRO
        self.user.plan_selected_at = timezone.now()
        self.user.plan_access_status = "EXPIRED"
        self.user.save(update_fields=["plan_tier", "plan_selected_at", "plan_access_status"])
        BillingPlanPrice.objects.create(
            tier=UserPlanTier.PRO,
            currency="pln",
            amount=40000,
            stripe_price_id="price_pro_renewal",
        )
        BillingSubscription.objects.create(
            user=self.user,
            tier=UserPlanTier.PRO,
            stripe_customer_id="cus_returning",
            stripe_subscription_id="sub_expired",
            status="canceled",
            current_period_end=timezone.now() - timedelta(days=1),
        )
        checkout_create.return_value = SimpleNamespace(url="https://checkout.stripe.test/renew")

        response = self.confirmed_checkout(UserPlanTier.PRO)

        self.assertRedirects(response, "https://checkout.stripe.test/renew", fetch_redirect_response=False)
        self.assertEqual(checkout_create.call_args.kwargs["customer"], "cus_returning")
        self.assertTrue(Organization.objects.filter(pk=company.pk).exists())
        self.user.refresh_from_db()
        self.assertEqual(self.user.plan_tier, UserPlanTier.PRO)
        self.assertEqual(self.user.plan_access_status, "EXPIRED")

    def test_active_subscription_redirects_old_upgrade_url_to_plan_management(self):
        self.user.plan_tier = UserPlanTier.PLUS
        self.user.save(update_fields=["plan_tier"])
        self.client.logout()
        self.client.force_login(User.objects.get(pk=self.user.pk))
        BillingSubscription.objects.create(
            user=self.user,
            tier=UserPlanTier.PLUS,
            stripe_customer_id="cus_test_123",
            stripe_subscription_id="sub_test_123",
            status="active",
            current_period_end=timezone.now() + timedelta(days=365),
        )

        response = self.client.get(f"{reverse('dashboard:plan-update')}?upgrade=1")

        self.assertRedirects(response, reverse("dashboard:billing-portal"))

    def test_pro_subscription_has_no_upgrade_option(self):
        self.user.plan_tier = UserPlanTier.PRO
        self.user.save(update_fields=["plan_tier"])
        BillingSubscription.objects.create(
            user=self.user,
            tier=UserPlanTier.PRO,
            stripe_customer_id="cus_test_123",
            stripe_subscription_id="sub_test_123",
            status="active",
            current_period_end=timezone.now() + timedelta(days=365),
        )

        response = self.client.get(reverse("dashboard:billing-portal"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, f"{reverse('dashboard:plan-update')}?upgrade=1")

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    @patch("apps.dashboard.views.stripe.Subscription.modify")
    @patch("apps.dashboard.views.stripe.Subscription.retrieve")
    @patch("apps.dashboard.views.stripe.checkout.Session.retrieve")
    def test_legacy_upgrade_callback_does_not_charge_again(
        self,
        mock_checkout_retrieve,
        mock_subscription_retrieve,
        mock_subscription_modify,
    ):
        pro_price = BillingPlanPrice.objects.create(
            tier=UserPlanTier.PRO,
            stripe_price_id="price_pro_pln",
            amount=40000,
            currency="pln",
            active_for_new_customers=True,
        )
        self.user.plan_tier = UserPlanTier.PLUS
        self.user.plan_selected_at = timezone.now()
        self.user.paid_plan_started_at = timezone.now()
        self.user.save(update_fields=["plan_tier", "plan_selected_at", "paid_plan_started_at"])
        BillingSubscription.objects.create(
            user=self.user,
            tier=UserPlanTier.PLUS,
            stripe_customer_id="cus_test_123",
            stripe_subscription_id="sub_test_123",
            status="active",
            current_period_end=timezone.now() + timedelta(days=365),
        )
        mock_checkout_retrieve.return_value = SimpleNamespace(
            metadata={
                "user_id": str(self.user.pk),
                "plan_tier": UserPlanTier.PRO,
                "upgrade_type": "plus_to_pro",
                "billing_plan_price_id": str(pro_price.pk),
                "stripe_subscription_id": "sub_test_123",
            },
            payment_status="paid",
        )
        mock_subscription_retrieve.return_value = SimpleNamespace(
            items=SimpleNamespace(data=[SimpleNamespace(id="si_test_123")])
        )
        mock_subscription_modify.return_value = SimpleNamespace(
            id="sub_test_123",
            customer="cus_test_123",
            status="active",
            current_period_start=1767225600,
            current_period_end=1798761600,
            cancel_at_period_end=False,
            canceled_at=None,
            latest_invoice="in_test_123",
            metadata={"user_id": str(self.user.pk), "plan_tier": UserPlanTier.PRO},
            items=SimpleNamespace(data=[{"price": {"id": pro_price.stripe_price_id}}]),
        )

        response = self.client.get(reverse("dashboard:plan-checkout-success"), {"session_id": "cs_upgrade_123"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:billing-portal"))
        mock_subscription_modify.assert_not_called()
        self.user.refresh_from_db()
        self.assertEqual(self.user.plan_tier, UserPlanTier.PLUS)

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    @patch("stripe.Invoice.retrieve")
    @patch("apps.dashboard.views.stripe.Subscription.retrieve")
    @patch("apps.dashboard.views.stripe.checkout.Session.retrieve")
    def test_checkout_success_reconciles_paid_invoice(
        self,
        mock_checkout_retrieve,
        mock_subscription_retrieve,
        mock_invoice_retrieve,
    ):
        price = BillingPlanPrice.objects.create(
            tier=UserPlanTier.BASIC,
            stripe_price_id="price_basic_checkout",
            amount=10000,
            currency="pln",
        )
        mock_checkout_retrieve.return_value = SimpleNamespace(
            mode="subscription",
            status="complete",
            subscription="sub_checkout_paid",
            metadata={"user_id": str(self.user.pk), "plan_tier": UserPlanTier.BASIC},
        )
        mock_subscription_retrieve.return_value = SimpleNamespace(
            id="sub_checkout_paid",
            customer="cus_checkout_paid",
            status="active",
            current_period_start=1767225600,
            current_period_end=1798761600,
            cancel_at_period_end=False,
            canceled_at=None,
            latest_invoice="in_checkout_paid",
            metadata={"user_id": str(self.user.pk), "plan_tier": UserPlanTier.BASIC},
            items=SimpleNamespace(data=[{"price": {"id": price.stripe_price_id}}]),
        )
        mock_invoice_retrieve.return_value = {
            "id": "in_checkout_paid",
            "subscription": "sub_checkout_paid",
            "customer": "cus_checkout_paid",
            "payment_intent": "pi_checkout_paid",
            "amount_paid": 10000,
            "currency": "pln",
            "status": "paid",
            "status_transitions": {"paid_at": 1790278292},
        }

        response = self.client.get(
            reverse("dashboard:plan-checkout-success"),
            {"session_id": "cs_checkout_paid"},
        )

        self.assertRedirects(response, reverse("dashboard:home"))
        payment = self.user.billing_payments.get(stripe_invoice_id="in_checkout_paid")
        self.assertEqual(payment.status, "paid")
        self.assertEqual(payment.amount_paid, 10000)
        subscription = self.user.billing_subscription
        self.assertIsNotNone(subscription.latest_payment_at)

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    @patch("apps.dashboard.views.stripe.checkout.Session.retrieve")
    def test_checkout_without_subscription_does_not_activate(self, mock_checkout_retrieve):
        mock_checkout_retrieve.return_value = SimpleNamespace(
            metadata={"user_id": str(self.user.pk), "plan_tier": UserPlanTier.PLUS},
            payment_status="paid",
        )

        response = self.client.get(
            reverse("dashboard:plan-checkout-success"),
            {"session_id": "cs_test_123"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:billing-portal"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.plan_tier, UserPlanTier.BASIC)

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    @patch("apps.dashboard.views.stripe.checkout.Session.retrieve")
    def test_checkout_success_rejects_session_for_other_user(self, mock_checkout_retrieve):
        mock_checkout_retrieve.return_value = SimpleNamespace(
            metadata={"user_id": str(self.other_user.pk), "plan_tier": UserPlanTier.PRO},
            payment_status="paid",
        )

        response = self.client.get(
            reverse("dashboard:plan-checkout-success"),
            {"session_id": "cs_test_123"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:plan-update"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.plan_tier, UserPlanTier.BASIC)

    def test_plan_downgrade_is_not_available(self):
        self.user.plan_tier = UserPlanTier.PLUS
        self.user.save(update_fields=["plan_tier"])
        BillingSubscription.objects.create(
            user=self.user,
            tier=UserPlanTier.PLUS,
            stripe_customer_id="cus_downgrade",
            stripe_subscription_id="sub_downgrade",
            status="active",
            current_period_end=timezone.now() + timedelta(days=365),
        )
        Organization.objects.create(owner=self.user, name="A", slug="a")
        Organization.objects.create(owner=self.user, name="B", slug="b")

        response = self.client.post(
            reverse("dashboard:plan-update"),
            {"plan_tier": UserPlanTier.BASIC},
        )

        self.assertRedirects(response, reverse("dashboard:billing-portal"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.plan_tier, UserPlanTier.PLUS)

    def test_user_can_delete_own_organization(self):
        organization = Organization.objects.create(owner=self.user, name="Delete me", slug="delete-me")

        response = self.client.post(reverse("dashboard:organization-delete", args=[organization.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:home"))
        self.assertFalse(Organization.objects.filter(pk=organization.pk).exists())

    def test_user_cannot_delete_other_user_organization(self):
        foreign_organization = Organization.objects.create(
            owner=self.other_user,
            name="Foreign",
            slug="foreign",
        )

        response = self.client.post(reverse("dashboard:organization-delete", args=[foreign_organization.pk]))

        self.assertEqual(response.status_code, 404)
        self.assertTrue(Organization.objects.filter(pk=foreign_organization.pk).exists())


class LanguageSwitchTests(TestCase):
    def test_localized_set_language_route_has_polish_prefix(self):
        self.assertEqual(reverse("set_language_localized"), "/set-language/")

        with override("pl"):
            self.assertEqual(reverse("set_language_localized"), "/pl/set-language/")

    def test_switching_from_polish_url_to_english_removes_prefix(self):
        response = self.client.post(
            "/pl/set-language/",
            {"language": "en", "next": "/pl/dashboard/organizations/new/"},
            HTTP_HOST="testserver",
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/dashboard/organizations/new/")
        self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, "en")

    def test_switching_from_english_url_to_polish_adds_prefix(self):
        response = self.client.post(
            "/set-language/",
            {"language": "pl", "next": "/dashboard/organizations/new/"},
            HTTP_HOST="testserver",
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/pl/dashboard/organizations/new/")
        self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, "pl")


class SellerManagementTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="strong-pass-123",
        )
        self.client_user = User.objects.create_user(
            username="client",
            email="client@example.com",
            password="strong-pass-123",
        )
        self.seller = User.objects.create_user(
            username="seller-home",
            email="seller-home@example.com",
            password="strong-pass-123",
            account_type=AccountType.STAFF,
        )

    def test_seller_sees_dedicated_home_layout(self):
        self.client.force_login(self.seller)

        response = self.client.get(reverse("dashboard:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("dashboard:seller-clients"))
        self.assertContains(response, reverse("dashboard:seller-prospects"))
        self.assertNotContains(response, reverse("dashboard:plan-update"))
        self.assertNotContains(response, "0/1")

    def test_admin_can_open_seller_list(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("dashboard:seller-list"))

        self.assertEqual(response.status_code, 200)

    def test_admin_home_shows_reports_quick_action(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("dashboard:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("dashboard:report-seller-activities"))

    def test_admin_report_shows_activity_counts_and_month_filter(self):
        other_seller = User.objects.create_user(
            username="seller-second",
            email="seller-second@example.com",
            password="strong-pass-123",
            account_type=AccountType.STAFF,
        )
        prospect_one = ProspectClient.objects.create(
            seller=self.seller,
            company_name="Lead One",
            contact_person="Alice",
            email="alice@example.com",
            phone="123456789",
        )
        prospect_two = ProspectClient.objects.create(
            seller=other_seller,
            company_name="Lead Two",
            contact_person="Bob",
            email="bob@example.com",
            phone="123456789",
        )
        ProspectActivity.objects.create(
            prospect=prospect_one,
            seller=self.seller,
            activity_type="call",
            activity_date="2026-03-10",
            activity_description="March call",
        )
        ProspectActivity.objects.create(
            prospect=prospect_one,
            seller=self.seller,
            activity_type="email",
            activity_date="2026-03-15",
            activity_description="March email",
        )
        ProspectActivity.objects.create(
            prospect=prospect_two,
            seller=other_seller,
            activity_type="meeting",
            activity_date="2026-04-02",
            activity_description="April meeting",
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse("dashboard:report-seller-activities"), {"month": "2026-03"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "seller-home")
        self.assertContains(response, ">2<", html=False)
        self.assertContains(response, "seller-second")
        self.assertContains(response, ">0<", html=False)
        self.assertContains(response, 'value="2026-03"')
        self.assertContains(response, 'id="seller-activity-chart"')

    def test_admin_report_defaults_to_current_month(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("dashboard:report-seller-activities"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'value="{timezone.localdate():%Y-%m}"')

    def test_admin_report_can_show_all_history(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("dashboard:report-seller-activities"), {"scope": "all"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ca\u0142a historia")

    def test_admin_can_create_seller_with_login_and_password(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("dashboard:seller-list"),
            {
                "username": "seller-one",
                "email": "seller-one@example.com",
                "password1": "strong-pass-123",
                "password2": "strong-pass-123",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:seller-list"))
        seller = User.objects.get(username="seller-one")
        self.assertEqual(seller.account_type, AccountType.STAFF)
        self.assertEqual(seller.email, "seller-one@example.com")
        self.assertTrue(seller.is_active)

    def test_admin_can_block_seller_access(self):
        seller = User.objects.create_user(
            username="seller-to-block",
            email="seller-to-block@example.com",
            password="strong-pass-123",
            account_type=AccountType.STAFF,
            is_active=True,
        )
        self.client.force_login(self.admin)

        response = self.client.post(reverse("dashboard:seller-toggle-access", args=[seller.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:seller-detail", args=[seller.pk]))
        seller.refresh_from_db()
        self.assertFalse(seller.is_active)

    def test_admin_can_delete_seller(self):
        seller = User.objects.create_user(
            username="seller-to-delete",
            email="seller-to-delete@example.com",
            password="strong-pass-123",
            account_type=AccountType.STAFF,
            is_active=True,
        )
        self.client.force_login(self.admin)

        response = self.client.post(reverse("dashboard:seller-delete", args=[seller.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:seller-list"))
        self.assertFalse(User.objects.filter(pk=seller.pk).exists())

    def test_non_admin_cannot_access_seller_management(self):
        self.client.force_login(self.client_user)

        response = self.client.get(reverse("dashboard:seller-list"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:home"))

    def test_sellers_are_hidden_on_client_list(self):
        seller = User.objects.create_user(
            username="seller-hidden",
            email="seller-hidden@example.com",
            password="strong-pass-123",
            account_type=AccountType.STAFF,
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse("dashboard:client-list"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, seller.email)

    def test_seller_can_link_prospect_with_registered_client(self):
        client_user = User.objects.create_user(
            username="client-linked",
            email="client-linked@example.com",
            password="strong-pass-123",
            account_type=AccountType.CLIENT,
        )
        prospect = ProspectClient.objects.create(
            seller=self.seller,
            company_name="Lead Corp",
            contact_person="Alice",
            email="alice@lead.example",
            phone="123456789",
        )
        self.client.force_login(self.seller)

        response = self.client.post(
            reverse("dashboard:prospect-link-client", args=[prospect.pk]),
            {"registered_client": client_user.pk},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:prospect-detail", args=[prospect.pk]))
        prospect.refresh_from_db()
        self.assertEqual(prospect.registered_client, client_user)

    def test_seller_cannot_link_other_seller_prospect(self):
        other_seller = User.objects.create_user(
            username="seller-other",
            email="seller-other@example.com",
            password="strong-pass-123",
            account_type=AccountType.STAFF,
        )
        client_user = User.objects.create_user(
            username="client-target",
            email="client-target@example.com",
            password="strong-pass-123",
            account_type=AccountType.CLIENT,
        )
        prospect = ProspectClient.objects.create(
            seller=other_seller,
            company_name="Foreign Lead",
            contact_person="Bob",
            email="bob@lead.example",
            phone="123456789",
        )
        self.client.force_login(self.seller)

        response = self.client.post(
            reverse("dashboard:prospect-link-client", args=[prospect.pk]),
            {"registered_client": client_user.pk},
        )

        self.assertEqual(response.status_code, 404)
        prospect.refresh_from_db()
        self.assertIsNone(prospect.registered_client)

    def test_seller_can_select_registered_client_while_creating_prospect(self):
        client_user = User.objects.create_user(
            username="client-at-create",
            email="client-at-create@example.com",
            password="strong-pass-123",
            account_type=AccountType.CLIENT,
        )
        self.client.force_login(self.seller)

        response = self.client.post(
            reverse("dashboard:prospect-create"),
            {
                "company_name": "Create Lead",
                "contact_person": "Eve",
                "email": "eve@lead.example",
                "phone": "999999999",
                "notes": "created with linked client",
                "registered_client": client_user.pk,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:seller-prospects"))

        prospect = ProspectClient.objects.get(company_name="Create Lead")
        self.assertEqual(prospect.seller, self.seller)
        self.assertEqual(prospect.registered_client, client_user)

    def test_admin_client_list_shows_linked_seller_username(self):
        client_user = User.objects.create_user(
            username="client-for-admin-list",
            email="client-for-admin-list@example.com",
            password="strong-pass-123",
            account_type=AccountType.CLIENT,
        )
        ProspectClient.objects.create(
            seller=self.seller,
            registered_client=client_user,
            company_name="Lead for Admin List",
            contact_person="Ann",
            email="ann@lead.example",
            phone="123456789",
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse("dashboard:client-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.seller.username)

    def test_admin_sees_expired_plan_without_losing_historical_tier(self):
        client_user = User.objects.create_user(
            username="expired-pro-client",
            email="expired-pro-client@example.com",
            password="strong-pass-123",
            account_type=AccountType.CLIENT,
            plan_tier=UserPlanTier.PRO,
            plan_access_status="EXPIRED",
        )
        self.client.force_login(self.admin)

        list_response = self.client.get(reverse("dashboard:client-list"))
        detail_response = self.client.get(reverse("dashboard:client-detail", args=[client_user.pk]))

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(list_response, "PRO")
        self.assertTrue(
            "Plan ended" in list_response.content.decode()
            or "Plan wygas" in list_response.content.decode()
        )
        self.assertContains(detail_response, "PRO")
        self.assertTrue(
            "Payment required" in detail_response.content.decode()
            or "Brak p&#322;atno&#347;ci" in detail_response.content.decode()
        )

    def test_admin_can_sort_clients_by_plan_status_and_see_lifecycle_states(self):
        now = timezone.now()
        removed_client = User.objects.create_user(
            username="removed-plan-client",
            email="removed-plan-client@example.com",
            password="strong-pass-123",
            account_type=AccountType.CLIENT,
            plan_tier=UserPlanTier.PLUS,
            plan_access_status="EXPIRED",
        )
        ManualPlanOrder.objects.create(
            user=removed_client,
            tier=UserPlanTier.PLUS,
            amount=20000,
            currency="pln",
            status=ManualPlanOrderStatus.DISABLED,
            payment_reference="PLUS-REMOVED-NOW",
            payment_due_at=now - timedelta(days=20),
            access_until=now + timedelta(days=340),
            disabled_at=now,
            termination_acknowledged_at=now,
        )
        canceling_client = User.objects.create_user(
            username="canceling-plan-client",
            email="canceling-plan-client@example.com",
            password="strong-pass-123",
            account_type=AccountType.CLIENT,
            plan_tier=UserPlanTier.PRO,
            plan_access_status="ACTIVE",
        )
        BillingSubscription.objects.create(
            user=canceling_client,
            tier=UserPlanTier.PRO,
            stripe_subscription_id="sub_canceling_list",
            status="active",
            current_period_start=now - timedelta(days=300),
            current_period_end=now + timedelta(days=65),
            cancel_at_period_end=True,
        )
        renewed_client = User.objects.create_user(
            username="renewed-plan-client",
            email="renewed-plan-client@example.com",
            password="strong-pass-123",
            account_type=AccountType.CLIENT,
            plan_tier=UserPlanTier.BASIC,
            plan_access_status="ACTIVE",
        )
        ManualPlanOrder.objects.create(
            user=renewed_client,
            tier=UserPlanTier.BASIC,
            amount=15000,
            currency="pln",
            status=ManualPlanOrderStatus.DISABLED,
            payment_reference="BASIC-OLD-PERIOD",
            payment_due_at=now - timedelta(days=390),
            access_until=now - timedelta(days=25),
            disabled_at=now - timedelta(days=25),
        )
        ManualPlanOrder.objects.create(
            user=renewed_client,
            tier=UserPlanTier.BASIC,
            amount=15000,
            currency="pln",
            status=ManualPlanOrderStatus.PAID,
            payment_reference="BASIC-RENEWED-PERIOD",
            payment_due_at=now - timedelta(days=2),
            access_until=now + timedelta(days=363),
            paid_at=now - timedelta(days=2),
        )
        overdue_client = User.objects.create_user(
            username="overdue-manual-client",
            email="overdue-manual-client@example.com",
            password="strong-pass-123",
            account_type=AccountType.CLIENT,
            plan_tier=UserPlanTier.PLUS,
            plan_access_status="EXPIRED",
        )
        ManualPlanOrder.objects.create(
            user=overdue_client,
            tier=UserPlanTier.PLUS,
            amount=20000,
            currency="pln",
            status=ManualPlanOrderStatus.AWAITING_PAYMENT,
            payment_reference="PLUS-PAYMENT-OVERDUE",
            payment_due_at=now - timedelta(minutes=1),
            access_until=now + timedelta(days=353),
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse("dashboard:client-list"), {"sort": "plan_status"})

        self.assertEqual(response.status_code, 200)
        status_labels = {client.plan_status_label_en for client in response.context["clients"]}
        self.assertIn("Removed immediately", status_labels)
        self.assertIn("Renewal turned off", status_labels)
        self.assertIn("Renewed · active", status_labels)
        self.assertIn("Payment overdue", status_labels)
        self.assertIn("sort=-plan_status", response.content.decode())
        ranks = [client.plan_status_rank for client in response.context["clients"]]
        self.assertEqual(ranks, sorted(ranks))

    @override_settings(ADMIN_MFA_REQUIRED=False)
    def test_admin_distinguishes_pro_manual_from_stripe_pro(self):
        now = timezone.now()
        self.client_user.plan_tier = UserPlanTier.PRO
        self.client_user.save(update_fields=["plan_tier"])
        ManualPlanOrder.objects.create(
            user=self.client_user,
            tier=UserPlanTier.PRO,
            amount=48000,
            currency="pln",
            status=ManualPlanOrderStatus.AWAITING_PAYMENT,
            payment_reference="PRO-MANUAL-DISPLAY",
            payment_due_at=now + timedelta(days=12),
            access_until=now + timedelta(days=365),
        )
        stripe_client = User.objects.create_user(
            username="stripe-pro-client",
            email="stripe-pro-client@example.com",
            password="strong-pass-123",
            account_type=AccountType.CLIENT,
            plan_tier=UserPlanTier.PRO,
        )
        BillingSubscription.objects.create(
            user=stripe_client,
            tier=UserPlanTier.PRO,
            stripe_subscription_id="sub_pro_display",
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=365),
        )
        self.client.force_login(self.admin)

        listing = self.client.get(reverse("dashboard:client-list"))
        manual_detail = self.client.get(reverse("dashboard:client-detail", args=[self.client_user.pk]))
        stripe_detail = self.client.get(reverse("dashboard:client-detail", args=[stripe_client.pk]))

        self.assertContains(listing, "PRO MANUAL")
        self.assertContains(manual_detail, "PRO MANUAL")
        self.assertContains(stripe_detail, ">PRO<")
        self.assertNotContains(stripe_detail, "PRO MANUAL")

    def test_admin_client_list_shows_last_invoice_date(self):
        client_user = User.objects.create_user(
            username="client-with-last-invoice",
            email="client-with-last-invoice@example.com",
            password="strong-pass-123",
            account_type=AccountType.CLIENT,
        )
        BillingInvoice.objects.create(
            user=client_user,
            issued_at="2026-07-05",
            sent=True,
            sent_at="2026-07-08",
            invoice_number="FV/LAST/001",
            document=SimpleUploadedFile("last-invoice.pdf", b"%PDF-1.4 last", content_type="application/pdf"),
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse("dashboard:client-list"), {"sort": "-invoice"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ostatnia faktura")
        self.assertContains(response, "2026-07-08")

    def test_admin_client_list_shows_verified_badge_when_client_has_verified_organization(self):
        client_user = User.objects.create_user(
            username="client-verified",
            email="client-verified@example.com",
            password="strong-pass-123",
            account_type=AccountType.CLIENT,
        )
        Organization.objects.create(
            owner=client_user,
            name="Verified Org",
            slug="verified-org",
            verification_status=VerificationStatus.HUMAN_ADMIN_VERIFIED,
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse("dashboard:client-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Zweryfikowano")

    def test_admin_can_verify_client_from_client_list_action(self):
        client_user = User.objects.create_user(
            username="client-to-verify",
            email="client-to-verify@example.com",
            password="strong-pass-123",
            account_type=AccountType.CLIENT,
        )
        organization = Organization.objects.create(
            owner=client_user,
            name="Needs Verification",
            slug="needs-verification",
            verification_status=VerificationStatus.UNVERIFIED,
        )
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("dashboard:client-verify", args=[client_user.pk]),
            {"next": reverse("dashboard:client-list")},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:client-list"))
        organization.refresh_from_db()
        self.assertEqual(organization.verification_status, VerificationStatus.HUMAN_ADMIN_VERIFIED)
        self.assertIsNotNone(organization.verified_at)
        self.assertEqual(organization.verified_by, self.admin)

    def test_seller_clients_list_shows_linked_seller_username(self):
        client_user = User.objects.create_user(
            username="client-for-seller-list",
            email="client-for-seller-list@example.com",
            password="strong-pass-123",
            account_type=AccountType.CLIENT,
        )
        ProspectClient.objects.create(
            seller=self.seller,
            registered_client=client_user,
            company_name="Lead for Seller List",
            contact_person="Tom",
            email="tom@lead.example",
            phone="123456789",
        )
        self.client.force_login(self.seller)

        response = self.client.get(reverse("dashboard:seller-clients"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.seller.username)

    def test_client_tables_show_activities_link_for_linked_client(self):
        client_user = User.objects.create_user(
            username="client-with-activity-link",
            email="client-with-activity-link@example.com",
            password="strong-pass-123",
            account_type=AccountType.CLIENT,
        )
        prospect = ProspectClient.objects.create(
            seller=self.seller,
            registered_client=client_user,
            company_name="Lead With Activity Link",
            contact_person="Lia",
            email="lia@lead.example",
            phone="123456789",
        )
        self.client.force_login(self.seller)

        seller_response = self.client.get(reverse("dashboard:seller-clients"))
        self.assertContains(seller_response, reverse("dashboard:prospect-detail", args=[prospect.pk]))

        self.client.force_login(self.admin)
        admin_response = self.client.get(reverse("dashboard:client-list"))
        self.assertContains(admin_response, reverse("dashboard:prospect-detail", args=[prospect.pk]))

    def test_admin_can_open_prospect_detail_for_linked_client(self):
        client_user = User.objects.create_user(
            username="client-admin-open",
            email="client-admin-open@example.com",
            password="strong-pass-123",
            account_type=AccountType.CLIENT,
        )
        prospect = ProspectClient.objects.create(
            seller=self.seller,
            registered_client=client_user,
            company_name="Lead Admin Open",
            contact_person="Meg",
            email="meg@lead.example",
            phone="123456789",
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse("dashboard:prospect-detail", args=[prospect.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Lead Admin Open")

    def test_admin_settlements_page_shows_only_paid_clients(self):
        paid_client = User.objects.create_user(
            username="paid-client",
            email="paid-client@example.com",
            password="strong-pass-123",
            account_type=AccountType.CLIENT,
            plan_tier=UserPlanTier.PLUS,
        )
        free_client = User.objects.create_user(
            username="free-client",
            email="free-client@example.com",
            password="strong-pass-123",
            account_type=AccountType.CLIENT,
            plan_tier=UserPlanTier.BASIC,
        )
        ProspectClient.objects.create(
            seller=self.seller,
            registered_client=paid_client,
            company_name="Paid Lead",
            contact_person="Paul",
            email="paul@lead.example",
            phone="123456789",
        )
        ProspectClient.objects.create(
            seller=self.seller,
            registered_client=free_client,
            company_name="Free Lead",
            contact_person="Frank",
            email="frank@lead.example",
            phone="123456789",
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse("dashboard:seller-settlements"), {"seller": self.seller.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, paid_client.username)
        self.assertNotContains(response, free_client.username)

    def test_admin_can_settle_paid_client_and_move_to_report(self):
        paid_client = User.objects.create_user(
            username="paid-client-settle",
            email="paid-client-settle@example.com",
            password="strong-pass-123",
            account_type=AccountType.CLIENT,
            plan_tier=UserPlanTier.PRO,
        )
        prospect = ProspectClient.objects.create(
            seller=self.seller,
            registered_client=paid_client,
            company_name="Paid Settle Lead",
            contact_person="Sara",
            email="sara@lead.example",
            phone="123456789",
        )
        self.client.force_login(self.admin)

        response = self.client.post(reverse("dashboard:seller-settlement-create", args=[prospect.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:seller-settlements"))
        settlement = SellerSettlement.objects.get(client=paid_client)
        self.assertEqual(settlement.seller, self.seller)

        page = self.client.get(reverse("dashboard:seller-settlements"), {"seller": self.seller.pk})
        self.assertNotContains(page, "Paid Settle Lead")
        self.assertContains(page, paid_client.email)
