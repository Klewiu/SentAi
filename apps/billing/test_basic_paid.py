from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from apps.accounts.models import User
from apps.companies.models import Organization, VerificationStatus
from apps.dashboard.views import create_manual_plan_order
from .access import effective_tier, has_publication_access
from .models import BillingProfile, BillingPlanPrice, BillingSubscription, ManualPlanOrder
from .services import sync_subscription_from_stripe, get_active_plan_price


class PaidBasicTests(TestCase):
    def test_admin_can_set_custom_basic_price(self):
        from apps.dashboard.forms import BillingPlanPriceForm
        form = BillingPlanPriceForm(instance=self.price, data={
            "tier": "BASIC", "amount": "80.00", "currency": "pln",
            "interval": "year", "stripe_price_id": "price_basic_updated",
            "active_for_new_customers": True,
        })
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(get_active_plan_price("BASIC", "pln").amount, 8000)
        from .services import plan_price_label
        self.assertEqual(plan_price_label("BASIC", currency="pln"), "80 PLN")

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    def test_custom_basic_checkout_checks_database_amount_against_stripe(self):
        self.price.amount = 8000
        self.price.save()
        remote = {"active": True, "currency": "pln", "unit_amount": 10000,
                  "recurring": {"interval": "year", "interval_count": 1}}
        with patch("stripe.Price.retrieve", return_value=remote), patch("stripe.checkout.Session.create") as create:
            self.client.post(reverse("dashboard:plan-update"), {"plan_tier": "BASIC", "subscription_terms_accepted": True})
            create.assert_not_called()
        remote["unit_amount"] = 8000
        with patch("stripe.Price.retrieve", return_value=remote), patch("stripe.checkout.Session.create", return_value={"id": "cs_custom_basic", "url": "https://checkout.stripe.com/custom-basic"}) as create:
            response = self.client.post(reverse("dashboard:plan-update"), {"plan_tier": "BASIC", "subscription_terms_accepted": True})
            self.assertEqual(response.url, "https://checkout.stripe.com/custom-basic")
            create.assert_called_once()

    def setUp(self):
        self.user = User.objects.create_user(username="paidbasic", email="paidbasic@example.com", password="test-password")
        BillingProfile.objects.create(user=self.user, company_name="Basic customer", tax_id="1234567890", street="Street 1", postal_code="00-001", city="Warsaw", country="PL", invoice_email=self.user.email)
        self.org = Organization.objects.create(owner=self.user, name="Paid Basic Profile", verification_status=VerificationStatus.HUMAN_ADMIN_VERIFIED)
        self.price = BillingPlanPrice.objects.create(tier="BASIC", amount=10000, currency="pln", interval="year", stripe_price_id="price_basic")
        self.client.force_login(self.user)

    def test_unpaid_basic_is_not_published_in_any_format_or_index(self):
        for suffix in ["json", "jsonld", "md", "llms"]:
            self.assertEqual(self.client.get(reverse("companies_api:public-company-" + suffix, args=[self.org.slug])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("sitemap-xml")), self.org.slug)
        self.assertNotContains(self.client.get(reverse("site-llms-txt")), self.org.slug)
        self.assertFalse(has_publication_access(self.user))

    def test_non_pro_manual_orders_are_rejected(self):
        for tier in ["BASIC", "PLUS"]:
            with self.subTest(tier=tier), self.assertRaises(ValueError):
                create_manual_plan_order(self.user, "pln", tier)
        self.assertFalse(ManualPlanOrder.objects.filter(user=self.user).exists())

    def test_removed_manual_option_is_rejected_and_stale_session_is_ignored(self):
        response = self.client.post(reverse("dashboard:plan-update"), {"plan_tier": "BASIC_MANUAL"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assertFalse(ManualPlanOrder.objects.exists())
        session = self.client.session
        session["manual_plan_tier"] = "BASIC"
        session.save()
        response = self.client.get(reverse("dashboard:manual-plan-confirm"))
        self.assertContains(response, "Pro Manual")
        self.client.post(reverse("dashboard:manual-plan-confirm"))
        self.assertEqual(ManualPlanOrder.objects.get(user=self.user).tier, "PRO")

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    def test_basic_checkout_requires_payment_and_annual_price(self):
        remote = {"active": True, "currency": "pln", "unit_amount": 10000, "recurring": {"interval": "year", "interval_count": 1}}
        with patch("stripe.Price.retrieve", return_value=remote), patch("stripe.checkout.Session.create", return_value={"id": "cs_basic", "url": "https://checkout.stripe.com/basic"}) as create:
            response = self.client.post(reverse("dashboard:plan-update"), {"plan_tier": "BASIC", "subscription_terms_accepted": True})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "https://checkout.stripe.com/basic")
        self.assertEqual(create.call_args.kwargs["line_items"], [{"price": "price_basic", "quantity": 1}])
        self.assertFalse(has_publication_access(self.user))
        self.user.refresh_from_db()
        self.assertIsNone(self.user.paid_plan_started_at)
        remote["recurring"]["interval"] = "month"
        with patch("stripe.Price.retrieve", return_value=remote), patch("stripe.checkout.Session.create") as create:
            self.client.post(reverse("dashboard:plan-update"), {"plan_tier": "BASIC", "subscription_terms_accepted": True})
            create.assert_not_called()

    def test_stripe_basic_sync_activates_and_expiry_removes_publication(self):
        subscription = {"id": "sub_basic", "customer": "cus_basic", "status": "active", "metadata": {"user_id": str(self.user.pk)}, "current_period_end": int((timezone.now() + timedelta(days=365)).timestamp()), "items": {"data": [{"price": {"id": self.price.stripe_price_id}}]}}
        sync_subscription_from_stripe(subscription)
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.paid_plan_started_at)
        self.assertIsNotNone(self.user.plan_selected_at)
        self.assertEqual(self.user.plan_tier, "BASIC")
        self.assertTrue(has_publication_access(self.user))
        for suffix in ["json", "jsonld", "md", "llms"]:
            self.assertEqual(self.client.get(reverse("companies_api:public-company-" + suffix, args=[self.org.slug])).status_code, 200)
        subscription["status"] = "canceled"
        sync_subscription_from_stripe(subscription)
        self.assertFalse(has_publication_access(self.user))

    def test_basic_uses_separate_eur_price(self):
        eur_price = BillingPlanPrice.objects.create(
            tier="BASIC", amount=2500, currency="eur", interval="year", stripe_price_id="price_basic_eur"
        )
        self.assertEqual(get_active_plan_price("BASIC", "pln"), self.price)
        self.assertEqual(get_active_plan_price("BASIC", "eur"), eur_price)

    def test_plan_pages_show_consistent_whole_prices_in_selected_currency(self):
        BillingPlanPrice.objects.create(tier="BASIC", amount=2500, currency="eur", interval="year", stripe_price_id="price_basic_eur")
        BillingPlanPrice.objects.create(tier="PLUS", amount=20000, currency="pln", interval="year", stripe_price_id="price_plus_pln")
        BillingPlanPrice.objects.create(tier="PLUS", amount=4600, currency="eur", interval="year", stripe_price_id="price_plus_eur")
        BillingPlanPrice.objects.create(tier="PRO", amount=40000, currency="pln", interval="year", stripe_price_id="price_pro_pln")
        BillingPlanPrice.objects.create(tier="PRO", amount=9300, currency="eur", interval="year", stripe_price_id="price_pro_eur")
        for route in ("landing", "dashboard:plan-update"):
            for currency, expected in (
                ("pln", ("100 PLN", "200 PLN", "400 PLN")),
                ("eur", ("25 EUR", "46 EUR", "93 EUR")),
            ):
                with self.subTest(route=route, currency=currency):
                    response = self.client.get(reverse(route), {"currency": currency})
                    for price in expected:
                        self.assertContains(response, price)
                        self.assertNotContains(response, price.replace(" ", ".00 "))

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    def test_eur_basic_checkout_validates_and_uses_eur_price(self):
        eur_price = BillingPlanPrice.objects.create(
            tier="BASIC", amount=2500, currency="eur", interval="year", stripe_price_id="price_basic_eur"
        )
        profile = self.user.billing_profile
        profile.country = "DE"
        profile.save(update_fields=["country"])
        remote = {"active": True, "currency": "eur", "unit_amount": 2500, "recurring": {"interval": "year", "interval_count": 1}}
        with patch("stripe.Price.retrieve", return_value=remote), patch(
            "stripe.checkout.Session.create",
            return_value={"id": "cs_basic_eur", "url": "https://checkout.stripe.com/basic-eur"},
        ) as create:
            response = self.client.post(
                reverse("dashboard:plan-update"),
                {"plan_tier": "BASIC", "billing_currency": "eur", "subscription_terms_accepted": True},
            )
        self.assertEqual(response.url, "https://checkout.stripe.com/basic-eur")
        self.assertEqual(create.call_args.kwargs["line_items"], [{"price": eur_price.stripe_price_id, "quantity": 1}])

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    def test_basic_upgrade_reuses_existing_subscription(self):
        from .services import upgrade_subscription
        pro = BillingPlanPrice.objects.create(tier="PLUS", amount=20000, currency="pln", stripe_price_id="price_plus")
        current = {"id": "sub_basic", "customer": "cus_basic", "status": "active", "metadata": {"user_id": str(self.user.pk)}, "current_period_start": int(timezone.now().timestamp()), "current_period_end": int((timezone.now() + timedelta(days=365)).timestamp()), "items": {"data": [{"id": "si_basic", "price": {"id": self.price.stripe_price_id}}]}}
        sync_subscription_from_stripe(current)
        pending = {**current, "pending_update": {"expires_at": 9999999999}, "latest_invoice": "in_upgrade_basic"}
        with patch("stripe.Subscription.retrieve", return_value=current), patch("stripe.Subscription.modify", return_value=pending) as modify, patch("stripe.Invoice.retrieve", return_value={"hosted_invoice_url": "https://invoice.stripe.com/basic"}), patch("apps.billing.services.record_invoice_payment"), patch("stripe.checkout.Session.create") as checkout:
            self.assertEqual(upgrade_subscription(self.user, pro), "https://invoice.stripe.com/basic")
            self.assertEqual(modify.call_args.args, ("sub_basic",))
            self.assertEqual(modify.call_args.kwargs["payment_behavior"], "pending_if_incomplete")
            checkout.assert_not_called()
        self.assertEqual(effective_tier(self.user), "BASIC")

    def test_pro_manual_payment_creates_invoice_notification(self):
        from apps.notifications.models import AdminNotification
        order = create_manual_plan_order(self.user, "pln", "PRO")
        admin = User.objects.create_superuser(username="basicadmin", email="basicadmin@example.com", password="test-password")
        self.client.force_login(admin)
        from apps.notifications.services import notify_invoice_needed_for_manual_order
        order.status = "paid"
        order.save()
        notify_invoice_needed_for_manual_order(order)
        notification = AdminNotification.objects.get(reference_key=f"manual-order:{order.pk}:invoice-needed")
        self.assertEqual(notification.title, "Pro Manual payment needs invoice")
