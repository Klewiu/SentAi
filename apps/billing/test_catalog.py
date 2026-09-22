from types import SimpleNamespace as Obj
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.urls import reverse
from apps.accounts.models import User
from .models import BillingPlanPrice, BillingSubscription
from .catalog import sync_prices, publish_price, cache_catalog, set_price_active, link_existing_prices


def price(id="price_new", amount=15000, active=True):
    return Obj(id=id, unit_amount=amount, currency="pln", active=active,
               lookup_key="xoaila_basic_pln_year", product="prod_basic",
               recurring={"interval": "year", "interval_count": 1},
               tax_behavior="unspecified")


@override_settings(STRIPE_SECRET_KEY="sk_test_catalog")
class CatalogTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username="catalog-admin", email="catalog@example.com", password="test-password")

    @patch("apps.billing.catalog.remote_catalog")
    def test_fresh_database_imports_stripe_amount_without_env_price_ids(self, remote):
        remote.return_value = [("BASIC", "pln", price())]
        self.assertEqual(sync_prices(), 1)
        self.assertEqual(BillingPlanPrice.objects.get().amount, 15000)
        sync_prices()
        self.assertEqual(BillingPlanPrice.objects.count(), 1)

    @patch("apps.billing.catalog.remote_catalog", side_effect=ValueError("connection failed"))
    def test_failed_sync_preserves_current_offer(self, remote):
        old = BillingPlanPrice.objects.create(tier="BASIC", currency="pln", amount=10000, stripe_price_id="price_old")
        with self.assertRaises(ValueError):
            sync_prices()
        old.refresh_from_db()
        self.assertTrue(old.active_for_new_customers)

    def test_invalid_catalog_rolls_back(self):
        bad = price()
        bad.recurring = {"interval": "month"}
        with self.assertRaises(ValueError):
            cache_catalog([("BASIC", "pln", bad)])
        self.assertEqual(BillingPlanPrice.objects.count(), 0)

    @patch("stripe.Price.create")
    @patch("apps.billing.catalog.remote_catalog")
    def test_increase_creates_remote_price_and_preserves_subscription(self, remote, create):
        old = BillingPlanPrice.objects.create(tier="BASIC", currency="pln", amount=10000, stripe_price_id="price_old")
        sub = BillingSubscription.objects.create(user=self.admin, tier="BASIC", plan_price=old, stripe_price_id=old.stripe_price_id)
        remote.side_effect = [[("BASIC", "pln", price("price_old", 10000))], [("BASIC", "pln", price())]]
        publish_price("BASIC", "pln", 15000, self.admin)
        self.assertEqual(create.call_args.kwargs["unit_amount"], 15000)
        self.assertTrue(create.call_args.kwargs["transfer_lookup_key"])
        old.refresh_from_db()
        sub.refresh_from_db()
        self.assertFalse(old.active_for_new_customers)
        self.assertEqual(old.amount, 10000)
        self.assertEqual(sub.plan_price_id, old.pk)
        self.assertEqual(sub.stripe_price_id, "price_old")

    @patch("stripe.Price.create")
    @patch("apps.billing.catalog.remote_catalog")
    def test_retry_does_not_create_duplicate_price(self, remote, create):
        remote.return_value = [("BASIC", "pln", price())]
        publish_price("BASIC", "pln", 15000)
        create.assert_not_called()

    @patch("stripe.Price.modify")
    @patch("stripe.Price.retrieve", return_value=price(amount=10000))
    @patch("apps.billing.catalog.remote_catalog", return_value=[])
    def test_link_rejects_wrong_amount_without_remote_write(self, remote, retrieve, modify):
        with self.assertRaises(ValueError):
            publish_price("BASIC", "pln", 15000, existing_id="price_new")
        modify.assert_not_called()

    @patch("stripe.Price.modify")
    @patch("stripe.Price.retrieve", return_value=price("price_old", 10000))
    @patch("apps.billing.catalog.remote_catalog")
    def test_archiving_updates_remote_and_local_availability(self, remote, retrieve, modify):
        old = BillingPlanPrice.objects.create(tier="BASIC", currency="pln", amount=10000, stripe_price_id="price_old")
        remote.return_value = [("BASIC", "pln", price("price_old", 10000, active=False))]
        set_price_active(old, False)
        modify.assert_called_once_with("price_old", active=False)
        old.refresh_from_db()
        self.assertFalse(old.active_for_new_customers)

    @patch("stripe.Price.modify")
    @patch("stripe.Price.retrieve")
    @patch("apps.billing.catalog.remote_catalog")
    def test_bootstrap_keeps_candidates_across_partial_imports(self, remote, retrieve, modify):
        first = price("price_basic", 10000)
        second = price("price_plus", 20000)
        second.lookup_key = None
        for tier, item in [("BASIC", first), ("PLUS", second)]:
            BillingPlanPrice.objects.create(tier=tier, currency="pln", amount=item.unit_amount, stripe_price_id=item.id)
        retrieve.side_effect = lambda key: first if key == first.id else second
        one = [("BASIC", "pln", first)]
        both = one + [("PLUS", "pln", second)]
        remote.side_effect = [[], [], one, one, both, both]
        self.assertEqual(link_existing_prices(), 2)
        self.assertEqual(modify.call_count, 2)
        self.assertEqual(BillingPlanPrice.objects.filter(active_for_new_customers=True).count(), 2)

    @patch("apps.billing.catalog.sync_prices", return_value=6)
    def test_sync_requires_admin_and_post(self, sync):
        url = reverse("dashboard:billing-price-sync")
        self.client.post(url)
        sync.assert_not_called()
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url).status_code, 302)
        sync.assert_called_once_with(self.admin)

    @patch("apps.billing.catalog.publish_price")
    def test_admin_form_converts_major_units_and_publishes(self, publish):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("dashboard:billing-price-management"), {
            "tier": "BASIC", "currency": "pln", "amount": "120.50",
        })
        self.assertEqual(response.status_code, 302)
        publish.assert_called_once_with("BASIC", "pln", 12050, self.admin, "")
