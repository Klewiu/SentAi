from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User, UserPlanTier
from apps.dashboard.views import create_manual_plan_order
from apps.notifications.models import AdminNotification

from .models import BillingPlanPrice, BillingProfile, ManualDiscountCoupon, ManualPlanOrder


class ManualDiscountCouponTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="coupon-customer",
            email="coupon@example.com",
            password="Strong-test-password-123!",
        )
        BillingProfile.objects.create(
            user=self.user,
            company_name="Coupon Company",
            tax_id="PL5260250995",
            street="Prosta 1",
            postal_code="00-001",
            city="Warszawa",
            country="PL",
            invoice_email="invoice@example.com",
        )
        for tier, amount in (("BASIC", 10000), ("PLUS", 20000), ("PRO", 40000)):
            BillingPlanPrice.objects.create(
                tier=tier,
                stripe_price_id=f"price_coupon_{tier.lower()}",
                amount=amount,
                currency="pln",
                interval="year",
                active_for_new_customers=True,
            )
        self.coupon = ManualDiscountCoupon.objects.create(
            code="pro-ten",
            discount_percent=10,
            max_redemptions=1,
        )

    def _start_pro_manual_purchase(self):
        session = self.client.session
        session["pending_plan_purchase"] = {
            "tier": UserPlanTier.PRO,
            "currency": "pln",
            "payment_method": "manual",
            "terms_accepted": True,
        }
        session.save()

    def test_pro_manual_order_stores_discount_snapshot_and_discounted_amount(self):
        order = create_manual_plan_order(self.user, "pln", "PRO", coupon_code="pro-ten")

        self.assertEqual(order.original_amount, 40000)
        self.assertEqual(order.amount, 36000)
        self.assertEqual(order.coupon_code, "PRO-TEN")
        self.assertEqual(order.discount_percent, 10)
        self.assertEqual(order.discount_coupon, self.coupon)
        self.assertEqual(order.admin_plan_label, "PRO MANUAL 10%")
        notification = AdminNotification.objects.get(reference_key=f"manual-order:{order.pk}:created")
        self.assertIn("Pro Manual 10%", notification.title)
        self.assertIn("Pro Manual 10%", notification.title_pl)

        admin = User.objects.create_superuser(
            username="plan-label-admin",
            email="plan-label-admin@example.com",
            password="Admin-test-password-123!",
        )
        self.client.force_login(admin)
        self.assertContains(self.client.get(reverse("dashboard:client-list")), "PRO MANUAL 10%")
        self.assertContains(
            self.client.get(reverse("dashboard:client-detail", args=[self.user.pk])),
            "PRO MANUAL 10%",
        )

    def test_coupon_cannot_be_used_for_basic_or_plus_manual(self):
        for tier in (UserPlanTier.BASIC, UserPlanTier.PLUS):
            with self.subTest(tier=tier), self.assertRaisesMessage(
                ValueError,
                "Discount coupons are available only for Pro Manual.",
            ):
                create_manual_plan_order(self.user, "pln", tier, coupon_code=self.coupon.code)
        self.assertFalse(ManualPlanOrder.objects.exists())

    def test_redemption_limit_is_enforced(self):
        create_manual_plan_order(self.user, "pln", "PRO", coupon_code=self.coupon.code)
        other = User.objects.create_user(username="other-customer", email="other@example.com")

        with self.assertRaisesMessage(ValueError, "invalid, expired, or has already been used"):
            create_manual_plan_order(other, "pln", "PRO", coupon_code=self.coupon.code)

    def test_expired_or_disabled_coupon_is_rejected(self):
        self.coupon.expires_at = timezone.now() - timedelta(minutes=1)
        self.coupon.save(update_fields=["expires_at", "updated_at"])
        with self.assertRaisesMessage(ValueError, "invalid, expired, or has already been used"):
            create_manual_plan_order(self.user, "pln", "PRO", coupon_code=self.coupon.code)

    def test_customer_applies_coupon_sees_price_and_confirms_order(self):
        self.client.force_login(self.user)
        self._start_pro_manual_purchase()
        confirm_url = reverse("dashboard:manual-plan-confirm")

        self.assertEqual(self.client.get(confirm_url).status_code, 200)
        response = self.client.post(
            confirm_url,
            {"action": "apply_coupon", "coupon_code": "pro-ten"},
        )
        self.assertRedirects(response, confirm_url)
        response = self.client.get(confirm_url)
        self.assertContains(response, "360 PLN")
        self.assertContains(response, "400 PLN")
        self.assertContains(response, "10%")

        response = self.client.post(confirm_url, {"action": "confirm"})
        self.assertRedirects(response, reverse("dashboard:billing-portal"))
        order = ManualPlanOrder.objects.get(user=self.user)
        self.assertEqual(order.amount, 36000)
        self.assertEqual(order.discount_percent, 10)

    def test_coupon_management_requires_admin_and_creates_coupon(self):
        url = reverse("dashboard:manual-discount-coupons")
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get(url), reverse("dashboard:home"))

        admin = User.objects.create_superuser(
            username="coupon-admin",
            email="admin@example.com",
            password="Admin-test-password-123!",
        )
        self.client.force_login(admin)
        self.assertContains(self.client.get(reverse("dashboard:home")), "Pro Manual coupons")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pro Manual discount coupons")
        response = self.client.post(
            url,
            {
                "code": "PRO-TWENTY",
                "discount_percent": "20",
                "max_redemptions": "3",
                "expires_at": "",
            },
        )
        self.assertRedirects(response, url)
        coupon = ManualDiscountCoupon.objects.get(code="PRO-TWENTY")
        self.assertEqual(coupon.discount_percent, 20)
        self.assertEqual(coupon.max_redemptions, 3)
        self.assertEqual(coupon.created_by, admin)

        response = self.client.post(
            reverse("dashboard:manual-discount-coupon-deactivate", args=[coupon.pk])
        )
        self.assertRedirects(response, url)
        coupon.refresh_from_db()
        self.assertFalse(coupon.active)
