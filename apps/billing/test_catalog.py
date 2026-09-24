from types import SimpleNamespace as Obj
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User

from .catalog import activate_price, archive_price, import_price
from .models import BillingPlanPrice, BillingSubscription


def remote_price(price_id="price_new", amount=15000, currency="pln", active=True, interval="year"):
    return Obj(
        id=price_id,
        unit_amount=amount,
        currency=currency,
        active=active,
        product="prod_basic",
        recurring={"interval": interval, "interval_count": 1, "usage_type": "licensed"},
        billing_scheme="per_unit",
        transform_quantity=None,
    )


@override_settings(STRIPE_SECRET_KEY="sk_test_catalog")
class CatalogTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="catalog-admin", email="catalog@example.com", password="test-password"
        )

    @patch("stripe.Price.retrieve")
    def test_product_id_is_rejected_before_stripe_request(self, retrieve):
        with self.assertRaisesMessage(ValueError, "Product ID"):
            import_price("BASIC", "prod_wrong", self.admin)
        retrieve.assert_not_called()
        self.assertFalse(BillingPlanPrice.objects.exists())

    @patch("stripe.Price.retrieve", return_value=remote_price(amount=2500, currency="eur"))
    def test_import_uses_amount_and_currency_returned_by_stripe(self, retrieve):
        local = import_price("BASIC", "price_new", self.admin)
        self.assertEqual(local.amount, 2500)
        self.assertEqual(local.currency, "eur")
        self.assertEqual(local.interval, "year")
        self.assertTrue(local.active_for_new_customers)

    @patch("stripe.Price.retrieve", return_value=remote_price())
    def test_replacement_preserves_old_subscription_price(self, retrieve):
        old = BillingPlanPrice.objects.create(
            tier="BASIC", currency="pln", amount=10000, stripe_price_id="price_old"
        )
        subscription = BillingSubscription.objects.create(
            user=self.admin, tier="BASIC", plan_price=old, stripe_price_id=old.stripe_price_id
        )
        new = import_price("BASIC", "price_new", self.admin)
        old.refresh_from_db()
        subscription.refresh_from_db()
        self.assertFalse(old.active_for_new_customers)
        self.assertTrue(new.active_for_new_customers)
        self.assertEqual(subscription.plan_price_id, old.pk)
        self.assertEqual(subscription.stripe_price_id, "price_old")

    @patch("stripe.Price.retrieve", return_value=remote_price(interval="month"))
    def test_non_annual_price_is_rejected_without_changing_catalog(self, retrieve):
        old = BillingPlanPrice.objects.create(
            tier="BASIC", currency="pln", amount=10000, stripe_price_id="price_old"
        )
        with self.assertRaises(ValueError):
            import_price("BASIC", "price_new", self.admin)
        old.refresh_from_db()
        self.assertTrue(old.active_for_new_customers)

    @patch("stripe.Price.retrieve", return_value=remote_price(active=False))
    def test_inactive_stripe_price_is_rejected(self, retrieve):
        with self.assertRaisesMessage(ValueError, "nieaktywna"):
            import_price("BASIC", "price_new", self.admin)

    @patch("stripe.Price.retrieve", return_value=remote_price())
    def test_same_price_cannot_be_assigned_to_another_plan(self, retrieve):
        BillingPlanPrice.objects.create(
            tier="PLUS", currency="pln", amount=15000, stripe_price_id="price_new"
        )
        with self.assertRaisesMessage(ValueError, "innego planu"):
            import_price("PRO", "price_new", self.admin)

    @patch("stripe.Price.modify")
    def test_archive_is_local_and_does_not_modify_stripe(self, modify):
        local = BillingPlanPrice.objects.create(
            tier="BASIC", currency="pln", amount=10000, stripe_price_id="price_old"
        )
        archive_price(local)
        local.refresh_from_db()
        self.assertFalse(local.active_for_new_customers)
        modify.assert_not_called()

    @patch("stripe.Price.retrieve", return_value=remote_price("price_old", 10000))
    def test_activation_verifies_stripe_and_replaces_current_local_offer(self, retrieve):
        current = BillingPlanPrice.objects.create(
            tier="BASIC", currency="pln", amount=15000, stripe_price_id="price_new"
        )
        old = BillingPlanPrice.objects.create(
            tier="BASIC", currency="pln", amount=10000, stripe_price_id="price_old",
            active_for_new_customers=False,
        )
        activate_price(old)
        current.refresh_from_db()
        old.refresh_from_db()
        self.assertFalse(current.active_for_new_customers)
        self.assertTrue(old.active_for_new_customers)

    @patch("apps.billing.catalog.import_price")
    def test_admin_imports_existing_price_through_form(self, import_mock):
        import_mock.return_value = BillingPlanPrice(
            tier="PLUS", currency="eur", amount=4600, stripe_price_id="price_plus_eur"
        )
        self.client.force_login(self.admin)
        response = self.client.post(reverse("dashboard:billing-price-management"), {
            "tier": "PLUS", "stripe_price_id": "price_plus_eur",
        })
        self.assertEqual(response.status_code, 302)
        import_mock.assert_called_once_with("PLUS", "price_plus_eur", self.admin)

    @patch("stripe.Price.retrieve")
    def test_admin_form_explains_product_id_error_without_api_call(self, retrieve):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("dashboard:billing-price-management"), {
            "tier": "PLUS", "stripe_price_id": "prod_wrong",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Product ID")
        retrieve.assert_not_called()
