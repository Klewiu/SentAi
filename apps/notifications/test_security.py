from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from apps.accounts.models import User
from apps.billing.models import BillingProfile, BillingSubscription
from .models import CustomerNotification
from .services import scan_customer_notifications, scan_admin_notifications


class NotificationLifecycleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="customer", email="customer@example.com", password="test-password")

    def test_billing_condition_resolves_and_reopens(self):
        scan_customer_notifications(self.user)
        profile = BillingProfile.objects.create(user=self.user, company_name="Company", tax_id="PL123", street="Street", postal_code="00-001", city="Warsaw", country="PL", invoice_email=self.user.email)
        notice = CustomerNotification.objects.get(reference_key=f"customer:{self.user.pk}:billing-incomplete")
        self.assertIsNotNone(notice.resolved_at)
        profile.tax_id = ""
        profile.save()
        scan_customer_notifications(User.objects.get(pk=self.user.pk))
        notice.refresh_from_db()
        self.assertIsNone(notice.closed_at)
        self.assertIsNone(notice.resolved_at)

    def test_canceling_subscription_does_not_claim_it_will_renew(self):
        BillingSubscription.objects.create(user=self.user, tier="PRO", status="active", cancel_at_period_end=True, current_period_end=timezone.now()+timedelta(days=5))
        scan_customer_notifications(self.user)
        self.assertFalse(CustomerNotification.objects.filter(reference_key__contains="stripe-renewal").exists())

    def test_notification_close_rejects_external_redirect(self):
        scan_customer_notifications(self.user)
        notice = self.user.customer_notifications.first()
        self.client.force_login(self.user)
        from apps.notifications.views import CustomerNotificationCloseView
        from django.test import RequestFactory
        from django.contrib.messages.storage.fallback import FallbackStorage
        request = RequestFactory().post("/", {"next": "https://attacker.example"})
        request.user = self.user
        request.session = {}
        request._messages = FallbackStorage(request)
        response = CustomerNotificationCloseView.as_view()(request, pk=notice.pk)
        self.assertEqual(response.url, reverse("dashboard:home"))
