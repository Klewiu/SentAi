from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import translation

from apps.accounts.models import User, UserPlanTier
from apps.billing.access import has_publication_access
from apps.billing.models import (
    AnnualPlanPaymentMethod,
    BillingPlanPrice,
    BillingProfile,
    ManualDiscountCoupon,
    ManualPlanOrder,
    ManualPlanOrderStatus,
)
from apps.billing.webhooks import process_event


@override_settings(
    SUBSCRIPTION_BILLING_ENABLED=False,
    BLIK_PAYMENTS_ENABLED=True,
    STRIPE_SECRET_KEY="sk_test_blik",
    STRIPE_WEBHOOK_SECRET="whsec_blik",
    SITE_BASE_URL="https://xoaila.example",
)
class BlikAnnualPaymentTests(TestCase):
    def setUp(self):
        translation.activate("en")
        self.user = User.objects.create_user(
            username="blik-customer",
            email="blik@example.com",
            password="Strong-test-password-123!",
        )
        self.profile = BillingProfile.objects.create(
            user=self.user,
            company_name="BLIK Test Sp. z o.o.",
            tax_id="PL5260250995",
            street="Prosta 1",
            postal_code="00-001",
            city="Warszawa",
            country="PL",
            invoice_email="faktury@example.com",
        )
        self.price = BillingPlanPrice.objects.create(
            tier=UserPlanTier.PLUS,
            currency="pln",
            amount=20000,
            stripe_price_id="price_plus_catalog",
        )
        self.client.force_login(self.user)

    def tearDown(self):
        translation.deactivate()

    def select_blik_and_accept_terms(self):
        selection = self.client.post(
            reverse("dashboard:plan-update"),
            {"plan_tier": UserPlanTier.PLUS, "billing_currency": "pln"},
        )
        self.assertRedirects(selection, reverse("dashboard:plan-payment-method"))
        method = self.client.post(
            reverse("dashboard:plan-payment-method"),
            {"payment_method": "blik"},
        )
        self.assertRedirects(
            method,
            reverse("dashboard:plan-purchase-continue"),
            fetch_redirect_response=False,
        )
        continuation = self.client.get(reverse("dashboard:plan-purchase-continue"))
        self.assertRedirects(continuation, reverse("dashboard:plan-purchase-terms"))
        acceptance = self.client.post(
            reverse("dashboard:plan-purchase-terms"),
            {"terms_accepted": "on"},
        )
        self.assertRedirects(acceptance, reverse("dashboard:blik-plan-confirm"))

    def test_billing_details_precede_payment_method_when_incomplete(self):
        self.profile.delete()
        response = self.client.post(
            reverse("dashboard:plan-update"),
            {"plan_tier": UserPlanTier.PLUS, "billing_currency": "pln"},
        )
        expected = f"{reverse('dashboard:billing-profile')}?next={reverse('dashboard:plan-payment-method')}"
        self.assertRedirects(response, expected, fetch_redirect_response=False)

    def test_manual_only_payment_screen_offers_blik_and_bank_transfer(self):
        self.client.post(
            reverse("dashboard:plan-update"),
            {"plan_tier": UserPlanTier.PLUS, "billing_currency": "pln"},
        )
        response = self.client.get(reverse("dashboard:plan-payment-method"))
        self.assertContains(response, "BLIK")
        self.assertContains(response, "Bank transfer")
        self.assertNotContains(response, "subscription")

    def test_checkout_is_one_time_blik_and_does_not_activate_before_payment(self):
        self.select_blik_and_accept_terms()
        confirmation = self.client.get(reverse("dashboard:blik-plan-confirm"))
        self.assertContains(confirmation, "Continue to Stripe and pay with BLIK")
        self.assertContains(confirmation, "does not renew automatically")

        checkout = {
            "id": "cs_test_blik_1",
            "url": "https://checkout.stripe.com/c/pay/cs_test_blik_1",
        }
        with patch("stripe.checkout.Session.create", return_value=checkout) as create:
            response = self.client.post(reverse("dashboard:blik-plan-confirm"))

        self.assertEqual(response.url, checkout["url"])
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["mode"], "payment")
        self.assertEqual(kwargs["payment_method_types"], ["blik"])
        self.assertEqual(kwargs["line_items"][0]["price_data"]["unit_amount"], 20000)
        self.assertNotIn("subscription_data", kwargs)

        order = ManualPlanOrder.objects.get(user=self.user)
        self.assertEqual(order.payment_method, AnnualPlanPaymentMethod.BLIK)
        self.assertEqual(order.status, ManualPlanOrderStatus.AWAITING_PAYMENT)
        self.assertEqual(order.stripe_checkout_session_id, "cs_test_blik_1")
        self.assertFalse(has_publication_access(self.user))

    def test_coupon_discount_is_shown_and_charged_for_blik(self):
        coupon = ManualDiscountCoupon.objects.create(
            code="PLUS-BLIK-20",
            discount_percent=20,
            max_redemptions=1,
        )
        self.select_blik_and_accept_terms()
        confirm_url = reverse("dashboard:blik-plan-confirm")

        response = self.client.post(
            confirm_url,
            {"action": "apply_coupon", "coupon_code": coupon.code.lower()},
        )
        self.assertRedirects(response, confirm_url)
        response = self.client.get(confirm_url)
        self.assertContains(response, "160 PLN")
        self.assertContains(response, "200 PLN")
        self.assertContains(response, "20%")

        checkout = {
            "id": "cs_test_blik_coupon",
            "url": "https://checkout.stripe.com/c/pay/cs_test_blik_coupon",
        }
        with patch("stripe.checkout.Session.create", return_value=checkout) as create:
            response = self.client.post(confirm_url, {"action": "confirm"})

        self.assertEqual(response.url, checkout["url"])
        self.assertEqual(
            create.call_args.kwargs["line_items"][0]["price_data"]["unit_amount"],
            16000,
        )
        order = ManualPlanOrder.objects.get(user=self.user)
        self.assertEqual(order.tier, UserPlanTier.PLUS)
        self.assertEqual(order.payment_method, AnnualPlanPaymentMethod.BLIK)
        self.assertEqual(order.original_amount, 20000)
        self.assertEqual(order.amount, 16000)
        self.assertEqual(order.coupon_code, "PLUS-BLIK-20")
        self.assertEqual(order.discount_percent, 20)
        self.assertEqual(order.admin_plan_label, "PLUS BLIK 20%")

    def test_paid_webhook_activates_once_and_creates_invoice_work_item(self):
        self.select_blik_and_accept_terms()
        with patch(
            "stripe.checkout.Session.create",
            return_value={"id": "cs_test_blik_paid", "url": "https://checkout.stripe.com/test"},
        ):
            self.client.post(reverse("dashboard:blik-plan-confirm"))
        order = ManualPlanOrder.objects.get(user=self.user)
        event = {
            "id": "evt_blik_paid_1",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_blik_paid",
                    "mode": "payment",
                    "status": "complete",
                    "payment_status": "paid",
                    "payment_intent": "pi_blik_paid_1",
                    "amount_total": 20000,
                    "currency": "pln",
                    "metadata": {
                        "payment_kind": "blik_annual",
                        "annual_order_id": str(order.pk),
                        "user_id": str(self.user.pk),
                        "plan_tier": UserPlanTier.PLUS,
                    },
                }
            },
        }

        process_event(event)
        process_event(event)

        order.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(order.status, ManualPlanOrderStatus.PAID)
        self.assertEqual(order.stripe_payment_intent_id, "pi_blik_paid_1")
        self.assertEqual(self.user.plan_tier, UserPlanTier.PLUS)
        self.assertTrue(has_publication_access(self.user))
        portal = self.client.get(reverse("dashboard:billing-portal"))
        self.assertContains(portal, "PLUS")
        self.assertNotContains(portal, "PLUS BLIK")
        self.assertNotContains(portal, "PLUS MANUAL")
        self.assertEqual(
            self.user.admin_notifications.filter(reference_key=f"manual-order:{order.pk}:invoice-needed").count(),
            1,
        )

    def test_wrong_webhook_amount_does_not_activate_plan(self):
        self.select_blik_and_accept_terms()
        with patch(
            "stripe.checkout.Session.create",
            return_value={"id": "cs_test_blik_wrong", "url": "https://checkout.stripe.com/test"},
        ):
            self.client.post(reverse("dashboard:blik-plan-confirm"))
        order = ManualPlanOrder.objects.get(user=self.user)
        event = {
            "id": "evt_blik_wrong_amount",
            "type": "checkout.session.completed",
            "data": {"object": {
                "id": "cs_test_blik_wrong",
                "mode": "payment",
                "payment_status": "paid",
                "payment_intent": "pi_wrong",
                "amount_total": 100,
                "currency": "pln",
                "metadata": {
                    "payment_kind": "blik_annual",
                    "annual_order_id": str(order.pk),
                    "user_id": str(self.user.pk),
                    "plan_tier": UserPlanTier.PLUS,
                },
            }},
        }
        with self.assertRaisesMessage(ValueError, "amount"):
            process_event(event)
        order.refresh_from_db()
        self.assertEqual(order.status, ManualPlanOrderStatus.AWAITING_PAYMENT)
        self.assertFalse(has_publication_access(self.user))
