from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from unittest.mock import patch

from apps.accounts.models import UserPlanTier
from apps.billing.models import BillingPayment, BillingSubscription
from apps.notifications.models import CustomerNotification, NotificationCategory


User = get_user_model()


class RegistrationFlowTests(TestCase):
    def test_register_creates_user_pending_email_verification(self):
        response = self.client.post(
            reverse("register"),
            {
                "username": "newclient",
                "company_name": "Acme Sp. z o.o.",
                "email": "client@example.com",
                "country": "Poland",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )

        self.assertRedirects(response, reverse("accounts:verify-notice"))
        user = User.objects.get(username="newclient")
        self.assertFalse(user.is_active)
        self.assertEqual(user.company_name, "Acme Sp. z o.o.")
        self.assertEqual(user.email, "client@example.com")
        self.assertEqual(user.country, "Poland")
        self.assertIsNotNone(user.date_joined)

    def test_last_login_is_updated_after_first_sign_in(self):
        User.objects.create_user(
            username="newclient",
            email="client@example.com",
            company_name="Acme Sp. z o.o.",
            password="StrongPass123!",
        )

        response = self.client.post(
            reverse("login"),
            {
                "username": "newclient",
                "password": "StrongPass123!",
            },
        )

        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username="newclient")
        self.assertIsNotNone(user.last_login)

    def test_profile_shows_close_account_action(self):
        user = User.objects.create_user(
            username="client",
            email="client@example.com",
            password="StrongPass123!",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("accounts:profile"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("accounts:close-account"))
        self.assertContains(response, "Close account")

    def test_close_account_soft_deletes_basic_user(self):
        user = User.objects.create_user(
            username="client-delete",
            email="client-delete@example.com",
            password="StrongPass123!",
        )
        self.client.force_login(user)

        response = self.client.post(reverse("accounts:close-account"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("login"))
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertIsNotNone(user.closed_at)
        self.assertEqual(user.closed_display_name, "client-delete")

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    @patch("apps.accounts.views.stripe.Subscription.delete")
    def test_close_account_cancels_active_subscription_before_soft_delete(self, mock_subscription_delete):
        user = User.objects.create_user(
            username="paid-delete",
            email="paid-delete@example.com",
            password="StrongPass123!",
            plan_tier=UserPlanTier.PLUS,
        )
        BillingSubscription.objects.create(
            user=user,
            tier=UserPlanTier.PLUS,
            stripe_customer_id="cus_test_123",
            stripe_subscription_id="sub_test_123",
            status="active",
        )
        self.client.force_login(user)

        response = self.client.post(reverse("accounts:close-account"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("login"))
        mock_subscription_delete.assert_called_once_with("sub_test_123")
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertIsNotNone(user.closed_at)
        self.assertEqual(user.closed_display_name, "paid-delete")

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    @patch("apps.accounts.views.stripe.Subscription.retrieve")
    @patch("apps.accounts.views.stripe.Subscription.delete", side_effect=RuntimeError("response lost"))
    def test_close_account_recovers_when_stripe_already_canceled(self, mock_delete, mock_retrieve):
        user = User.objects.create_user(
            username="paid-retry",
            email="paid-retry@example.com",
            password="StrongPass123!",
            plan_tier=UserPlanTier.PRO,
        )
        BillingSubscription.objects.create(
            user=user,
            tier=UserPlanTier.PRO,
            stripe_customer_id="cus_test_retry",
            stripe_subscription_id="sub_test_retry",
            status="active",
        )
        mock_retrieve.return_value = {
            "id": "sub_test_retry",
            "customer": "cus_test_retry",
            "status": "canceled",
        }
        self.client.force_login(user)

        response = self.client.post(reverse("accounts:close-account"))

        self.assertEqual(response.status_code, 302)
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertIsNotNone(user.closed_at)
        mock_delete.assert_called_once_with("sub_test_retry")
        mock_retrieve.assert_called_once_with("sub_test_retry")

    @override_settings(ADMIN_MFA_REQUIRED=False)
    def test_close_account_anonymizes_user_when_financial_records_must_remain(self):
        original_email = "financial-delete@example.com"
        user = User.objects.create_user(
            username="financial-delete",
            email=original_email,
            company_name="Private Company",
            country="Poland",
            password="StrongPass123!",
        )
        subscription = BillingSubscription.objects.create(
            user=user,
            tier=UserPlanTier.PRO,
            stripe_subscription_id="sub_canceled",
            status="canceled",
        )
        BillingPayment.objects.create(
            user=user,
            subscription=subscription,
            stripe_invoice_id="in_financial_delete",
            amount_paid=10000,
        )
        CustomerNotification.objects.create(
            user=user,
            title="Open before closure",
            category=NotificationCategory.CUSTOMER,
        )
        self.client.force_login(user)

        response = self.client.post(reverse("accounts:close-account"))

        self.assertEqual(response.status_code, 302)
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertIsNotNone(user.closed_at)
        self.assertEqual(user.closed_display_name, "Private Company")
        self.assertNotEqual(user.email, original_email)
        self.assertEqual(user.company_name, "")
        self.assertEqual(user.country, "")
        self.assertFalse(user.has_usable_password())
        self.assertFalse(User.objects.filter(email=original_email).exists())
        self.assertFalse(user.customer_notifications.filter(closed_at__isnull=True).exists())

        admin = User.objects.create_superuser(
            username="closure-admin",
            email="closure-admin@example.com",
            password="StrongPass123!",
        )
        self.client.force_login(admin)
        admin_response = self.client.get(reverse("dashboard:client-list"))
        self.assertContains(admin_response, "Private Company")
        self.assertContains(admin_response, f"#{user.pk}")
        self.assertContains(admin_response, "Account closed")
