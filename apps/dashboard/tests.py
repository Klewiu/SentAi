import json
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
        self.assertContains(response, "Pro Manual ends in 30 days")
        self.assertContains(response, "Pro Manual ends in 14 days")

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

    def create_billing_profile(self, user=None, country="PL"):
        user = user or self.user
        return BillingProfile.objects.create(
            user=user,
            customer_type="company",
            company_name="Client Company",
            tax_id="1234567890",
            street="Test Street 1",
            postal_code="00-001",
            city="Warsaw",
            country=country,
            invoice_email=user.email,
        )

    def test_add_company_button_visible_when_under_limit(self):
        response = self.client.get(reverse("dashboard:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("dashboard:organization-create"))

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
        self.assertContains(response, "BASIC")
        self.assertContains(response, "client")
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
        self.create_billing_profile(country="PL")
        mock_checkout_create.return_value = SimpleNamespace(url="https://checkout.stripe.test/session")

        response = self.client.post(
            reverse("dashboard:plan-update"),
            {"plan_tier": UserPlanTier.PLUS, "billing_currency": "pln", "subscription_terms_accepted": "on"},
        )

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
    def test_user_selecting_paid_plan_must_accept_subscription_terms(self, mock_checkout_create):
        response = self.client.post(
            reverse("dashboard:plan-update"),
            {"plan_tier": UserPlanTier.PLUS},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You must accept the subscription terms")
        mock_checkout_create.assert_not_called()
        self.user.refresh_from_db()
        self.assertEqual(self.user.plan_tier, UserPlanTier.BASIC)

    @override_settings(STRIPE_SECRET_KEY="")
    def test_user_can_open_subscription_management_page(self):
        BillingSubscription.objects.create(
            user=self.user,
            tier=UserPlanTier.PLUS,
            stripe_customer_id="cus_test_123",
            stripe_subscription_id="sub_test_123",
            status="active",
        )

        response = self.client.get(reverse("dashboard:billing-portal"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Manage subscription")
        self.assertContains(response, "PLUS")

    def test_customer_can_order_manual_pro_and_get_immediate_access(self):
        self.create_billing_profile(country="PL")

        response = self.client.post(
            reverse("dashboard:plan-update"),
            {"plan_tier": "PRO_MANUAL", "billing_currency": "pln", "subscription_terms_accepted": "on"},
        )

        self.assertRedirects(response, reverse("dashboard:manual-plan-confirm"))
        self.assertFalse(ManualPlanOrder.objects.filter(user=self.user).exists())

        confirmation = self.client.post(reverse("dashboard:manual-plan-confirm"))

        self.assertRedirects(confirmation, reverse("dashboard:plan-update"))
        self.user.refresh_from_db()
        order = ManualPlanOrder.objects.get(user=self.user)
        self.assertEqual(self.user.plan_tier, UserPlanTier.PRO)
        self.assertEqual(order.amount, 48000)
        self.assertEqual(order.currency, "pln")
        self.assertEqual(order.status, ManualPlanOrderStatus.AWAITING_PAYMENT)
        self.assertGreaterEqual((order.payment_due_at - order.created_at).days, 13)
        self.assertIn("Client-Company", order.payment_reference)

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

        self.assertRedirects(response, reverse("dashboard:plan-update"))
        mock_checkout_create.assert_not_called()

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
        self.assertEqual(self.user.plan_tier, UserPlanTier.BASIC)
        self.assertEqual(order.status, ManualPlanOrderStatus.DISABLED)
        self.assertEqual(order.disabled_by, admin)

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
        admin = User.objects.create_superuser("turnover-admin", "turnover-admin@example.com", "strong-pass-123")
        self.client.force_login(admin)

        confirmation = self.client.post(reverse("dashboard:manual-plan-mark-paid", args=[order.pk]))

        self.assertRedirects(confirmation, reverse("dashboard:billing-overview"))
        order.refresh_from_db()
        self.assertEqual(order.status, ManualPlanOrderStatus.PAID)
        overview = self.client.get(reverse("dashboard:billing-overview"))
        self.assertContains(overview, "480.00 PLN", count=4)

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
            amount_paid=40000,
            currency="pln",
            status="paid",
            paid_at=timezone.now(),
        )
        admin = User.objects.create_superuser("invoice-task-admin", "invoice-task-admin@example.com", "strong-pass-123")
        self.client.force_login(admin)

        page = self.client.get(reverse("dashboard:billing-invoices-admin"))

        self.assertContains(page, "Upload invoice", count=2)
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
        self.assertFalse(BillingInvoice.objects.filter(payment=first).exists())
        detail = self.client.get(reverse("dashboard:billing-customer-invoices", args=[self.user.pk]))
        self.assertContains(detail, "FV/STRIPE/002")
        self.assertContains(detail, "2026-07-08")

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
    @patch("apps.dashboard.views.stripe.Subscription.modify")
    def test_user_can_cancel_subscription_renewal_at_period_end(self, mock_subscription_modify):
        subscription = BillingSubscription.objects.create(
            user=self.user,
            tier=UserPlanTier.PLUS,
            stripe_customer_id="cus_test_123",
            stripe_subscription_id="sub_test_123",
            status="active",
        )

        response = self.client.post(reverse("dashboard:billing-subscription-cancel"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:billing-portal"))
        mock_subscription_modify.assert_called_once_with("sub_test_123", cancel_at_period_end=True)
        subscription.refresh_from_db()
        self.assertTrue(subscription.cancel_at_period_end)

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    @patch("apps.dashboard.views.stripe.Subscription.modify")
    def test_user_can_reactivate_subscription_renewal(self, mock_subscription_modify):
        subscription = BillingSubscription.objects.create(
            user=self.user,
            tier=UserPlanTier.PLUS,
            stripe_customer_id="cus_test_123",
            stripe_subscription_id="sub_test_123",
            status="active",
            cancel_at_period_end=True,
        )

        response = self.client.post(reverse("dashboard:billing-subscription-reactivate"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:billing-portal"))
        mock_subscription_modify.assert_called_once_with("sub_test_123", cancel_at_period_end=False)
        subscription.refresh_from_db()
        self.assertFalse(subscription.cancel_at_period_end)

    def test_admin_can_add_billing_plan_price(self):
        admin = User.objects.create_superuser(
            username="admin-price",
            email="admin-price@example.com",
            password="strong-pass-123",
        )
        self.client.force_login(admin)

        response = self.client.post(
            reverse("dashboard:billing-price-management"),
            {
                "tier": UserPlanTier.PLUS,
                "stripe_price_id": "price_plus_79",
                "amount": "79.00",
                "currency": "pln",
                "interval": "year",
                "active_for_new_customers": "on",
                "notes": "new price",
            },
        )

        self.assertEqual(response.status_code, 302)
        price = BillingPlanPrice.objects.get(stripe_price_id="price_plus_79")
        self.assertEqual(price.amount, 7900)

    def test_admin_can_keep_active_pln_and_eur_prices_for_same_plan(self):
        admin = User.objects.create_superuser(
            username="admin-currency",
            email="admin-currency@example.com",
            password="strong-pass-123",
        )
        BillingPlanPrice.objects.create(
            tier=UserPlanTier.PLUS,
            stripe_price_id="price_plus_pln",
            amount=7900,
            currency="pln",
            active_for_new_customers=True,
        )
        self.client.force_login(admin)

        response = self.client.post(
            reverse("dashboard:billing-price-management"),
            {
                "tier": UserPlanTier.PLUS,
                "stripe_price_id": "price_plus_eur",
                "amount": "19.00",
                "currency": "eur",
                "interval": "year",
                "active_for_new_customers": "on",
                "notes": "eur price",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            BillingPlanPrice.objects.filter(tier=UserPlanTier.PLUS, active_for_new_customers=True).count(),
            2,
        )

    def test_admin_can_edit_archived_price_with_same_stripe_price_id(self):
        admin = User.objects.create_superuser(
            username="admin-price-edit",
            email="admin-price-edit@example.com",
            password="strong-pass-123",
        )
        price = BillingPlanPrice.objects.create(
            tier=UserPlanTier.PLUS,
            stripe_price_id="price_existing_eur",
            amount=50,
            currency="eur",
            active_for_new_customers=False,
        )
        self.client.force_login(admin)

        response = self.client.post(
            reverse("dashboard:billing-price-edit", args=[price.pk]),
            {
                "tier": UserPlanTier.PLUS,
                "stripe_price_id": "price_existing_eur",
                "amount": "46.00",
                "currency": "eur",
                "interval": "year",
                "active_for_new_customers": "on",
                "notes": "corrected local amount",
            },
        )

        self.assertEqual(response.status_code, 302)
        price.refresh_from_db()
        self.assertEqual(price.amount, 4600)
        self.assertTrue(price.active_for_new_customers)

    def test_admin_can_activate_archived_price(self):
        admin = User.objects.create_superuser(
            username="admin-price-activate",
            email="admin-price-activate@example.com",
            password="strong-pass-123",
        )
        price = BillingPlanPrice.objects.create(
            tier=UserPlanTier.PRO,
            stripe_price_id="price_pro_pln",
            amount=40000,
            currency="pln",
            active_for_new_customers=False,
        )
        self.client.force_login(admin)

        response = self.client.post(reverse("dashboard:billing-price-activate", args=[price.pk]))

        self.assertEqual(response.status_code, 302)
        price.refresh_from_db()
        self.assertTrue(price.active_for_new_customers)

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

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
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

        response = self.client.post(
            reverse("dashboard:plan-update"),
            {
                "plan_tier": UserPlanTier.PLUS,
                "billing_currency": "eur",
                "subscription_terms_accepted": "on",
            },
        )

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

        response = self.client.post(
            reverse("dashboard:plan-update"),
            {
                "plan_tier": UserPlanTier.PLUS,
                "billing_currency": "pln",
                "subscription_terms_accepted": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:billing-profile"))
        mock_checkout_create.assert_not_called()

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
        self.assertContains(response, "VAT ID is required")

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

    def test_billing_profile_normalizes_polish_vat_id_with_country_prefix(self):
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

        self.assertEqual(response.status_code, 302)
        self.user.billing_profile.refresh_from_db()
        self.assertEqual(self.user.billing_profile.tax_id, "PL5260250274")

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
        self.assertContains(response, "VAT ID country prefix must match")

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

        response = self.client.post(
            reverse("dashboard:plan-update"),
            {
                "plan_tier": UserPlanTier.PLUS,
                "billing_currency": "eur",
                "subscription_terms_accepted": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        _, kwargs = mock_checkout_create.call_args
        self.assertEqual(kwargs["line_items"][0]["price"], "price_plus_pln")

    @override_settings(STRIPE_WEBHOOK_SECRET="")
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

        response = self.client.post(
            reverse("stripe-webhook"),
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.plan_tier, UserPlanTier.PLUS)
        self.assertEqual(self.user.billing_subscription.stripe_subscription_id, "sub_test_123")

    @override_settings(STRIPE_WEBHOOK_SECRET="")
    def test_invoice_paid_webhook_records_renewal_payment(self):
        subscription = BillingSubscription.objects.create(
            user=self.user,
            tier=UserPlanTier.PLUS,
            stripe_customer_id="cus_test_123",
            stripe_subscription_id="sub_test_123",
            status="active",
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
                    "hosted_invoice_url": "https://invoice.stripe.test/in_renewal_123",
                    "invoice_pdf": "https://invoice.stripe.test/in_renewal_123.pdf",
                    "status_transitions": {
                        "paid_at": 1798761600,
                    },
                }
            },
        }

        response = self.client.post(
            reverse("stripe-webhook"),
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payment = self.user.billing_payments.get(stripe_invoice_id="in_renewal_123")
        self.assertEqual(payment.subscription, subscription)
        self.assertEqual(payment.amount_paid, 20000)
        self.assertEqual(payment.currency, "pln")
        self.assertEqual(payment.status, "paid")
        subscription.refresh_from_db()
        self.assertEqual(subscription.latest_invoice_id, "in_renewal_123")
        self.assertIsNotNone(subscription.latest_payment_at)

    @override_settings(STRIPE_WEBHOOK_SECRET="")
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

        response = self.client.post(reverse("stripe-webhook"), data=json.dumps(payload), content_type="application/json")

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.plan_tier, UserPlanTier.BASIC)

    def test_user_can_downgrade_to_basic_without_payment(self):
        self.user.plan_tier = UserPlanTier.PLUS
        self.user.save(update_fields=["plan_tier"])

        response = self.client.post(
            reverse("dashboard:plan-update"),
            {"plan_tier": UserPlanTier.BASIC},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:home"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.plan_tier, UserPlanTier.BASIC)

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
        )

        response = self.client.post(
            reverse("dashboard:plan-update"),
            {"plan_tier": UserPlanTier.BASIC},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:billing-portal"))
        mock_subscription_modify.assert_not_called()
        self.user.refresh_from_db()
        subscription.refresh_from_db()
        self.assertEqual(self.user.plan_tier, UserPlanTier.PLUS)
        self.assertFalse(subscription.cancel_at_period_end)

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy", SITE_BASE_URL="http://testserver")
    @patch("apps.dashboard.views.stripe.checkout.Session.create")
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
        )
        mock_checkout_create.return_value = SimpleNamespace(url="https://checkout.stripe.test/upgrade")

        response = self.client.post(
            reverse("dashboard:plan-update"),
            {
                "plan_tier": UserPlanTier.PRO,
                "billing_currency": "pln",
                "subscription_terms_accepted": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "https://checkout.stripe.test/upgrade")
        _, kwargs = mock_checkout_create.call_args
        self.assertEqual(kwargs["mode"], "payment")
        self.assertEqual(kwargs["line_items"][0]["price_data"]["unit_amount"], 40000)
        self.assertEqual(kwargs["metadata"]["upgrade_type"], "plus_to_pro")

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

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    @patch("apps.dashboard.views.stripe.Subscription.modify")
    @patch("apps.dashboard.views.stripe.Subscription.retrieve")
    @patch("apps.dashboard.views.stripe.checkout.Session.retrieve")
    def test_plus_to_pro_upgrade_success_updates_existing_subscription(
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
        self.assertEqual(response.url, reverse("dashboard:home"))
        mock_subscription_modify.assert_called_once()
        _, kwargs = mock_subscription_modify.call_args
        self.assertEqual(kwargs["items"], [{"id": "si_test_123", "price": pro_price.stripe_price_id}])
        self.assertEqual(kwargs["billing_cycle_anchor"], "now")
        self.assertEqual(kwargs["proration_behavior"], "none")
        self.user.refresh_from_db()
        self.assertEqual(self.user.plan_tier, UserPlanTier.PRO)

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    @patch("apps.dashboard.views.stripe.checkout.Session.retrieve")
    def test_checkout_success_updates_user_plan(self, mock_checkout_retrieve):
        mock_checkout_retrieve.return_value = SimpleNamespace(
            metadata={"user_id": str(self.user.pk), "plan_tier": UserPlanTier.PLUS},
            payment_status="paid",
        )

        response = self.client.get(
            reverse("dashboard:plan-checkout-success"),
            {"session_id": "cs_test_123"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:home"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.plan_tier, UserPlanTier.PLUS)

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

    def test_plan_downgrade_is_blocked_if_user_has_too_many_pages(self):
        self.user.plan_tier = UserPlanTier.PLUS
        self.user.save(update_fields=["plan_tier"])
        Organization.objects.create(owner=self.user, name="A", slug="a")
        Organization.objects.create(owner=self.user, name="B", slug="b")

        response = self.client.post(
            reverse("dashboard:plan-update"),
            {"plan_tier": UserPlanTier.BASIC},
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.plan_tier, UserPlanTier.PLUS)
        self.assertContains(response, "Please reduce to 1 or fewer")

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
            {"language": "en", "next": "/pl/organizations/new/"},
            HTTP_HOST="testserver",
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/organizations/new/")
        self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, "en")

    def test_switching_from_english_url_to_polish_adds_prefix(self):
        response = self.client.post(
            "/set-language/",
            {"language": "pl", "next": "/organizations/new/"},
            HTTP_HOST="testserver",
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/pl/organizations/new/")
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
        self.assertContains(response, "All history")

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
        self.assertContains(response, "Last invoice")
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
        self.assertContains(response, "Verified")

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
