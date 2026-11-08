from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone, translation

from apps.accounts.models import User, UserPlanTier
from apps.billing.catalog import set_public_plan_price
from apps.billing.models import BillingPlanPrice


@override_settings(SUBSCRIPTION_BILLING_ENABLED=False, INTERNATIONAL_BILLING_ENABLED=False)
class PublicPriceChangeTests(TestCase):
    def setUp(self):
        translation.activate("en")
        self.admin = User.objects.create_superuser(
            username="price-admin-local",
            email="price-admin-local@example.com",
            password="Admin-test-password-123!",
        )
        self.client.force_login(self.admin)

    def tearDown(self):
        translation.deactivate()

    def test_admin_can_replace_public_price_without_stripe_price(self):
        response = self.client.post(reverse("dashboard:billing-price-management"), {
            "tier": UserPlanTier.PLUS,
            "gross_amount_pln": "200.00",
            "notes": "Initial public price",
        })
        self.assertRedirects(response, reverse("dashboard:billing-price-management"))
        price = BillingPlanPrice.objects.get(tier=UserPlanTier.PLUS, active_for_new_customers=True)
        self.assertEqual(price.amount, 20000)
        self.assertTrue(price.stripe_price_id.startswith("local_"))
        self.assertEqual(price.notes, "Initial public price")

    def test_reduction_preserves_history_and_uses_lowest_applied_price_from_30_days(self):
        old = BillingPlanPrice.objects.create(
            tier=UserPlanTier.PRO,
            stripe_price_id="local_old_pro",
            amount=40000,
            currency="pln",
            effective_from=timezone.now() - timedelta(days=20),
        )
        reduced = set_public_plan_price(UserPlanTier.PRO, 35000, actor=self.admin)
        old.refresh_from_db()
        self.assertFalse(old.active_for_new_customers)
        self.assertIsNotNone(old.effective_until)
        self.assertEqual(reduced.previous_amount, 40000)
        self.assertEqual(reduced.lowest_amount_30_days, 40000)
        self.assertTrue(reduced.shows_price_reduction)

        landing = self.client.get(reverse("landing"))
        self.assertContains(landing, "Lowest price in the 30 days before the reduction:")
        self.assertContains(landing, "400 PLN")

    def test_price_increase_does_not_render_reduction_disclosure(self):
        BillingPlanPrice.objects.create(
            tier=UserPlanTier.BASIC,
            stripe_price_id="local_old_basic",
            amount=10000,
            currency="pln",
        )
        increased = set_public_plan_price(UserPlanTier.BASIC, 12000, actor=self.admin)
        self.assertFalse(increased.shows_price_reduction)
        page = self.client.get(reverse("landing"))
        self.assertNotContains(page, "Lowest price in the 30 days before the reduction:")

    def test_drop_above_recent_low_is_not_advertised_as_a_reduction(self):
        now = timezone.now()
        BillingPlanPrice.objects.create(
            tier=UserPlanTier.PLUS,
            stripe_price_id="local_recent_low_plus",
            amount=15000,
            currency="pln",
            active_for_new_customers=False,
            effective_from=now - timedelta(days=20),
            effective_until=now - timedelta(days=10),
        )
        BillingPlanPrice.objects.create(
            tier=UserPlanTier.PLUS,
            stripe_price_id="local_current_plus",
            amount=22000,
            currency="pln",
            effective_from=now - timedelta(days=10),
        )
        changed = set_public_plan_price(UserPlanTier.PLUS, 18000, actor=self.admin)
        self.assertFalse(changed.shows_price_reduction)
        self.assertIsNone(changed.lowest_amount_30_days)
