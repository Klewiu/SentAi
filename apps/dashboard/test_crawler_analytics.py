from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import BillingSubscription
from apps.companies.models import CrawlerAnalyticsDailyStat, Organization
from apps.notifications.models import AdminNotification, CustomerNotification


User = get_user_model()


class CrawlerAnalyticsDashboardTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="analytics-admin",
            email="analytics-admin@example.com",
            password="strong-pass-123",
        )
        self.owner = User.objects.create_user(
            username="analytics-owner",
            email="analytics-owner@example.com",
            password="strong-pass-123",
            preferred_language="pl",
        )
        BillingSubscription.objects.create(
            user=self.owner,
            tier=self.owner.plan_tier,
            status="active",
            current_period_end=timezone.now() + timedelta(days=365),
        )
        self.other_owner = User.objects.create_user(
            username="analytics-other-owner",
            email="analytics-other-owner@example.com",
            password="strong-pass-123",
        )
        self.organization = Organization.objects.create(
            owner=self.owner,
            name="Shared Analytics Co",
            slug="shared-analytics-co",
        )
        self.other_organization = Organization.objects.create(
            owner=self.other_owner,
            name="Private Analytics Co",
            slug="private-analytics-co",
            crawler_analytics_shared=True,
        )

    @override_settings(DEFAULT_FROM_EMAIL="kontakt@xoaila.pl", SITE_BASE_URL="https://xoaila.pl")
    def test_admin_can_toggle_collection_and_share_only_a_populated_report(self):
        self.client.force_login(self.admin)
        report_url = reverse("dashboard:crawler-analytics-admin", args=[self.organization.pk])
        settings_url = reverse("dashboard:crawler-analytics-admin-settings", args=[self.organization.pk])

        self.assertEqual(self.client.get(report_url).status_code, 200)
        response = self.client.post(settings_url, {"action": "toggle_sharing"})
        self.assertRedirects(response, report_url)
        self.organization.refresh_from_db()
        self.assertFalse(self.organization.crawler_analytics_shared)
        self.assertEqual(len(mail.outbox), 0)

        self.client.post(settings_url, {"action": "toggle_collection"})
        self.organization.refresh_from_db()
        self.assertTrue(self.organization.crawler_analytics_enabled)
        collection_notice = AdminNotification.objects.get(title="Crawler analytics collection enabled")
        self.assertIn("Shared Analytics Co", collection_notice.message_pl)
        self.assertEqual(
            collection_notice.action_url,
            reverse("dashboard:crawler-analytics-admin", args=[self.organization.pk]),
        )

        CrawlerAnalyticsDailyStat.objects.create(
            organization=self.organization,
            date=timezone.localdate() - timedelta(days=1),
            bot_key="oai-searchbot",
            bot_name="ChatGPT Search",
            bot_category="ai_search",
            resource="company_json",
            request_count=1,
        )
        self.client.post(settings_url, {"action": "toggle_sharing"})
        self.organization.refresh_from_db()
        self.assertTrue(self.organization.crawler_analytics_shared)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].from_email, "kontakt@xoaila.pl")
        self.assertEqual(mail.outbox[0].to, [self.owner.email])
        self.assertIn("Raporty AI dla Shared Analytics Co są dostępne", mail.outbox[0].subject)
        self.assertIn("Ostatnie 7 dni: 1", mail.outbox[0].body)
        self.assertIn("Otwórz raporty", mail.outbox[0].body)
        shared_admin_notice = AdminNotification.objects.get(title="Crawler analytics report shared")
        shared_customer_notice = CustomerNotification.objects.get(
            user=self.owner,
            title="New crawler analytics report",
        )
        self.assertEqual(
            shared_admin_notice.action_url,
            reverse("dashboard:crawler-analytics-admin", args=[self.organization.pk]),
        )
        self.assertEqual(
            shared_customer_notice.action_url,
            reverse("dashboard:crawler-analytics-customer-organization", args=[self.organization.pk]),
        )
        self.assertEqual(
            AdminNotification.objects.filter(title="Crawler analytics report shared").count(),
            1,
        )
        self.assertEqual(
            CustomerNotification.objects.filter(user=self.owner, title="New crawler analytics report").count(),
            1,
        )

        self.client.post(settings_url, {"action": "toggle_collection"})
        self.organization.refresh_from_db()
        self.assertFalse(self.organization.crawler_analytics_enabled)
        self.assertTrue(self.organization.crawler_analytics_shared)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            AdminNotification.objects.filter(title="Crawler analytics report shared").count(),
            1,
        )
        self.assertEqual(
            CustomerNotification.objects.filter(user=self.owner, title="New crawler analytics report").count(),
            1,
        )

        response = self.client.post(settings_url, {"action": "toggle_sharing"}, follow=True)
        self.organization.refresh_from_db()
        self.assertFalse(self.organization.crawler_analytics_shared)
        self.assertEqual(len(mail.outbox), 1)
        self.assertContains(response, "No email was sent because a notification")
        self.assertEqual(
            AdminNotification.objects.filter(
                title="Crawler analytics report sharing revoked"
            ).count(),
            1,
        )
        self.assertEqual(
            CustomerNotification.objects.filter(
                user=self.owner,
                title="Crawler analytics report access revoked",
            ).count(),
            1,
        )
        revoked_notice = CustomerNotification.objects.get(
            user=self.owner,
            title="Crawler analytics report access revoked",
        )
        self.assertIn("Cofnięto dostęp", revoked_notice.message_pl)
        self.assertEqual(revoked_notice.action_url, "")

    @override_settings(DEFAULT_FROM_EMAIL="kontakt@xoaila.pl", SITE_BASE_URL="https://xoaila.pl")
    def test_revoking_report_sends_email_after_seven_day_window(self):
        CrawlerAnalyticsDailyStat.objects.create(
            organization=self.organization,
            date=timezone.localdate(),
            bot_key="oai-searchbot",
            bot_name="ChatGPT Search",
            bot_category="ai_search",
            resource="company_json",
            request_count=2,
        )
        self.organization.crawler_analytics_shared = True
        self.organization.crawler_analytics_last_notification_sent_at = timezone.now() - timedelta(days=8)
        self.organization.save(
            update_fields=[
                "crawler_analytics_shared",
                "crawler_analytics_last_notification_sent_at",
            ]
        )
        self.client.force_login(self.admin)

        self.client.post(
            reverse("dashboard:crawler-analytics-admin-settings", args=[self.organization.pk]),
            {"action": "toggle_sharing"},
        )

        self.organization.refresh_from_db()
        self.assertFalse(self.organization.crawler_analytics_shared)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("zostało cofnięte", mail.outbox[0].subject)
        self.assertIn("nie będzie już widoczny", mail.outbox[0].body)
        self.assertIn("Nie wyłącza zbierania danych", mail.outbox[0].body)

    def test_customer_sees_only_owned_shared_reports(self):
        CrawlerAnalyticsDailyStat.objects.create(
            organization=self.organization,
            date=timezone.localdate(),
            bot_key="oai-searchbot",
            bot_name="ChatGPT Search",
            bot_category="ai_search",
            resource="company_json",
            request_count=3,
        )
        self.client.force_login(self.owner)
        hidden_nav = self.client.get(reverse("dashboard:home"))
        self.assertNotContains(hidden_nav, reverse("dashboard:crawler-analytics-reports"))

        self.organization.crawler_analytics_shared = True
        self.organization.save(update_fields=["crawler_analytics_shared"])
        self.assertFalse(self.organization.crawler_analytics_enabled)

        visible_nav = self.client.get(reverse("dashboard:home"))
        self.assertContains(visible_nav, reverse("dashboard:crawler-analytics-reports"))
        detail_url = reverse(
            "dashboard:crawler-analytics-customer-organization",
            args=[self.organization.pk],
        )
        self.assertContains(visible_nav, detail_url)
        self.assertContains(visible_nav, "account-nav-menu-compact")
        response = self.client.get(reverse("dashboard:crawler-analytics-reports"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Shared Analytics Co")
        self.assertNotContains(response, "Private Analytics Co")
        self.assertContains(response, "3")
        self.assertContains(response, "table-fixed")
        self.assertContains(response, "sm:min-w-[32rem]")
        organization_response = self.client.get(detail_url)
        self.assertEqual(organization_response.status_code, 200)
        self.assertContains(organization_response, "Shared Analytics Co")
        self.assertNotContains(organization_response, "Private Analytics Co")

        self.client.force_login(self.other_owner)
        self.assertEqual(self.client.get(detail_url).status_code, 404)

    def test_client_cannot_access_administrative_report_controls(self):
        self.client.force_login(self.owner)
        response = self.client.get(
            reverse("dashboard:crawler-analytics-admin", args=[self.organization.pk])
        )

        self.assertRedirects(response, reverse("dashboard:home"))
