import hashlib
import hmac
import json
import time
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.companies.models import Organization, VerificationStatus
from .models import BillingPlanPrice, BillingSubscription, ManualPlanOrder


class BillingSecurityTests(TestCase):
    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    def test_repeated_checkout_reuses_open_session(self):
        from django.test import RequestFactory
        from apps.dashboard.views import PlanUpdateView
        from .models import BillingProfile
        BillingProfile.objects.create(user=self.user, company_name="Audit", country="PL", invoice_email=self.user.email)
        view = PlanUpdateView()
        view.request = RequestFactory().post("/")
        view.request.user = self.user
        session = {"id": "cs_reuse", "status": "open", "url": "https://checkout.stripe.com/test"}
        with patch("stripe.checkout.Session.create", return_value=session) as create, patch("stripe.checkout.Session.retrieve", return_value=session):
            self.assertEqual(view._create_checkout_session("PRO", self.price), session)
            self.assertEqual(view._create_checkout_session("PRO", self.price), session)
            create.assert_called_once()
            self.assertTrue(create.call_args.kwargs["idempotency_key"].startswith("checkout:"))

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_audit", STRIPE_SECRET_KEY="sk_test_dummy")
    def test_late_active_event_uses_current_canceled_state(self):
        from .services import sync_subscription_from_stripe
        sync_subscription_from_stripe(self.subscription)
        event = {"id": "evt_late", "type": "customer.subscription.updated", "data": {"object": self.subscription}}
        canceled = {**self.subscription, "status": "canceled"}
        with patch("stripe.Subscription.retrieve", return_value=canceled):
            self.assertEqual(self.post_event(event).status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.plan_tier, "BASIC")

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    def test_upgrade_uses_one_pending_subscription_invoice_and_reuses_it(self):
        from copy import deepcopy
        from .services import sync_subscription_from_stripe, upgrade_subscription
        BillingPlanPrice.objects.create(tier="PLUS", stripe_price_id="price_plus", amount=20000, currency="pln")
        current = deepcopy(self.subscription)
        current["items"]["data"][0]["price"]["id"] = "price_plus"
        current["current_period_start"] = int(time.time())
        sync_subscription_from_stripe(current)
        pending = deepcopy(current)
        pending.update(pending_update={"expires_at": int(time.time()) + 3600}, latest_invoice="in_upgrade")
        invoice = {"id": "in_upgrade", "subscription": "sub_audit", "customer": "cus_audit", "status": "open", "hosted_invoice_url": "https://invoice.stripe.com/test"}
        with patch("stripe.Subscription.retrieve", side_effect=[current, pending]), patch("stripe.Subscription.modify", return_value=pending) as modify, patch("stripe.Invoice.retrieve", return_value=invoice), patch("stripe.checkout.Session.create") as checkout:
            self.assertEqual(upgrade_subscription(self.user, self.price), invoice["hosted_invoice_url"])
            self.assertEqual(upgrade_subscription(self.user, self.price), invoice["hosted_invoice_url"])
            modify.assert_called_once()
            self.assertEqual(modify.call_args.kwargs["payment_behavior"], "pending_if_incomplete")
            self.assertEqual(modify.call_args.kwargs["proration_behavior"], "none")
            checkout.assert_not_called()
        self.user.refresh_from_db()
        self.assertEqual(self.user.plan_tier, "PLUS")
        paid = deepcopy(self.subscription)
        with patch("stripe.Subscription.retrieve", return_value=paid), patch("stripe.Subscription.modify") as modify:
            upgrade_subscription(self.user, self.price)
            modify.assert_not_called()
        self.user.refresh_from_db()
        self.assertEqual(self.user.plan_tier, "PRO")

    def test_price_metadata_cannot_grant_an_unmapped_plan(self):
        from .services import sync_subscription_from_stripe
        self.subscription["items"]["data"][0]["price"]["id"] = "price_unmapped"
        with self.assertRaises(ValueError):
            sync_subscription_from_stripe(self.subscription)
        self.assertFalse(BillingSubscription.objects.filter(user=self.user).exists())

    def setUp(self):
        self.user = User.objects.create_user(username="audit", email="audit@example.com", password="test-password", plan_selected_at=timezone.now())
        self.price = BillingPlanPrice.objects.create(tier="PRO", stripe_price_id="price_pro", amount=40000, currency="pln")
        self.subscription = {
            "id": "sub_audit", "object": "subscription", "customer": "cus_audit", "status": "active",
            "metadata": {"user_id": str(self.user.pk), "plan_tier": "PRO"},
            "current_period_end": int((timezone.now() + timedelta(days=365)).timestamp()),
            "items": {"data": [{"id": "si_audit", "price": {"id": "price_pro"}}]},
        }

    def post_event(self, event, signed=True):
        payload = json.dumps(event)
        timestamp = str(int(time.time()))
        digest = hmac.new(b"whsec_audit", f"{timestamp}.{payload}".encode(), hashlib.sha256).hexdigest()
        return self.client.post(reverse("stripe-webhook"), payload, content_type="application/json", HTTP_STRIPE_SIGNATURE=f"t={timestamp},v1={digest}" if signed else "")

    @override_settings(STRIPE_WEBHOOK_SECRET="")
    def test_missing_secret_fails_closed(self):
        response = self.post_event({"id": "evt_unsigned", "type": "customer.subscription.updated", "data": {"object": self.subscription}}, signed=False)
        self.assertEqual(response.status_code, 503)
        self.user.refresh_from_db()
        self.assertEqual(self.user.plan_tier, "BASIC")

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_audit", STRIPE_SECRET_KEY="sk_test_dummy")
    def test_signed_events_reconcile_once_and_retry_failures(self):
        event = {"id": "evt_valid", "type": "customer.subscription.updated", "data": {"object": self.subscription}}
        with patch("stripe.Subscription.retrieve", side_effect=RuntimeError("temporary failure")):
            self.assertEqual(self.post_event(event).status_code, 503)
        with patch("stripe.Subscription.retrieve", return_value=self.subscription) as retrieve:
            self.assertEqual(self.post_event(event).status_code, 200)
            self.assertEqual(self.post_event(event).status_code, 200)
            retrieve.assert_called_once()
        self.user.refresh_from_db()
        self.assertEqual(self.user.plan_tier, "PRO")

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_audit")
    def test_invalid_signature_cannot_change_access(self):
        self.assertEqual(self.post_event({"id": "evt_bad", "type": "customer.subscription.updated", "data": {"object": self.subscription}}, signed=False).status_code, 400)

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    def test_unpaid_checkout_and_lookup_failure_do_not_activate(self):
        self.client.force_login(self.user)
        session = {"id": "cs_audit", "mode": "subscription", "status": "complete", "payment_status": "unpaid", "subscription": "sub_audit", "customer": "cus_audit", "metadata": {"user_id": str(self.user.pk), "plan_tier": "PRO"}}
        with patch("stripe.checkout.Session.retrieve", return_value=session), patch("stripe.Subscription.retrieve", side_effect=RuntimeError("outage")):
            self.client.get(reverse("dashboard:plan-checkout-success"), {"session_id": "cs_audit"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.plan_tier, "BASIC")

    def test_expired_and_overdue_manual_access_is_denied_without_page_visit(self):
        self.user.plan_tier = "PRO"
        self.user.save()
        org = Organization.objects.create(owner=self.user, name="Audit", verification_status=VerificationStatus.HUMAN_ADMIN_VERIFIED)
        order = ManualPlanOrder.objects.create(user=self.user, amount=40000, currency="pln", payment_reference="audit", status="paid", payment_due_at=timezone.now()-timedelta(days=365), access_until=timezone.now()-timedelta(seconds=1))
        from .access import effective_tier
        self.assertEqual(effective_tier(self.user), "BASIC")
        self.assertEqual(self.client.get(reverse("companies_api:public-company-md", args=[org.slug])).status_code, 404)
        order.status = "awaiting_payment"
        order.access_until = timezone.now()+timedelta(days=300)
        order.save()
        self.assertEqual(effective_tier(self.user), "BASIC")
        self.assertEqual(self.client.get(reverse("companies_api:public-company-md", args=[org.slug])).status_code, 404)
