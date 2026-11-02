from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User, UserPlanTier
from apps.billing.models import BillingPlanPrice, BillingProfile


@override_settings(
    SUBSCRIPTION_BILLING_ENABLED=False,
    STRIPE_SECRET_KEY="sk_test_dormant",
    STRIPE_WEBHOOK_SECRET="whsec_dormant",
)
class ManualOnlyBillingModeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="manual-only-customer",
            email="manual-only@example.com",
            password="Strong-test-password-123!",
        )
        BillingProfile.objects.create(
            user=self.user,
            company_name="Manual Only Company",
            tax_id="PL5260250995",
            street="Prosta 1",
            postal_code="00-001",
            city="Warszawa",
            country="PL",
            invoice_email="manual-only@example.com",
        )
        for tier, amount in (
            (UserPlanTier.BASIC, 10000),
            (UserPlanTier.PLUS, 20000),
            (UserPlanTier.PRO, 40000),
        ):
            BillingPlanPrice.objects.create(
                tier=tier,
                currency="pln",
                amount=amount,
                stripe_price_id=f"price_dormant_{tier.lower()}",
            )
        self.client.force_login(self.user)

    def test_plan_selection_skips_payment_method_and_starts_manual_flow(self):
        response = self.client.post(
            reverse("dashboard:plan-update"),
            {"plan_tier": UserPlanTier.PLUS, "billing_currency": "pln"},
        )

        expected_next = reverse("dashboard:plan-purchase-continue")
        self.assertRedirects(
            response,
            f"{reverse('dashboard:billing-profile')}?next={expected_next}",
            fetch_redirect_response=False,
        )
        pending = self.client.session["pending_plan_purchase"]
        self.assertEqual(pending["payment_method"], "manual")
        self.assertFalse(pending["terms_accepted"])

        continuation = self.client.get(expected_next)
        self.assertRedirects(continuation, reverse("dashboard:plan-purchase-terms"))
        terms = self.client.get(reverse("dashboard:plan-purchase-terms"))
        self.assertContains(terms, "bank transfer")
        self.assertNotContains(terms, "Stripe")
        self.assertNotContains(terms, "subscription")

        acceptance = self.client.post(
            reverse("dashboard:plan-purchase-terms"),
            {"terms_accepted": "on"},
        )
        self.assertRedirects(acceptance, reverse("dashboard:manual-plan-confirm"))
        confirmation = self.client.get(reverse("dashboard:manual-plan-confirm"))
        self.assertContains(confirmation, "Confirm annual plan order")
        self.assertNotContains(confirmation, "Stripe")
        self.assertNotContains(confirmation, "subscription")

    def test_subscription_routes_and_price_editor_are_not_exposed(self):
        self.assertRedirects(
            self.client.get(reverse("dashboard:plan-payment-method")),
            reverse("dashboard:plan-update"),
        )
        self.assertRedirects(
            self.client.get(reverse("dashboard:stripe-plan-confirm")),
            reverse("dashboard:plan-update"),
        )
        self.assertEqual(
            self.client.post(reverse("stripe-webhook"), data=b"{}", content_type="application/json").status_code,
            404,
        )

        admin = User.objects.create_superuser(
            username="manual-only-admin",
            email="manual-only-admin@example.com",
            password="Admin-test-password-123!",
        )
        self.client.force_login(admin)
        self.assertRedirects(
            self.client.get(reverse("dashboard:billing-price-management")),
            reverse("dashboard:home"),
        )

    def test_published_manual_pages_do_not_render_subscription_copy(self):
        plan_page = self.client.get(reverse("dashboard:plan-update"))
        self.assertContains(plan_page, "annual plan paid by bank transfer")
        self.assertNotContains(plan_page, "Stripe")
        self.assertNotContains(plan_page, "subscription")

        admin = User.objects.create_superuser(
            username="manual-copy-admin",
            email="manual-copy-admin@example.com",
            password="Admin-test-password-123!",
        )
        self.client.force_login(admin)
        home = self.client.get(reverse("dashboard:home"))
        self.assertContains(home, "Manual Plans & Turnover")
        self.assertNotContains(home, "Subscriptions & Turnovers")
        self.assertNotContains(home, "Price management")

        overview = self.client.get(reverse("dashboard:billing-overview"))
        self.assertContains(overview, "Manual Plans & Turnover")
        self.assertNotContains(overview, "Stripe subscription")
        self.assertNotContains(overview, "No subscriptions yet")

        notifications = self.client.get(reverse("dashboard:notifications"))
        self.assertNotContains(notifications, '<option value="stripe">', html=False)
