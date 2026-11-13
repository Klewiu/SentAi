from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.companies.models import CrawlerAnalyticsDailyStat, Organization


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

    def test_admin_can_toggle_collection_and_share_only_a_populated_report(self):
        self.client.force_login(self.admin)
        report_url = reverse("dashboard:crawler-analytics-admin", args=[self.organization.pk])
        settings_url = reverse("dashboard:crawler-analytics-admin-settings", args=[self.organization.pk])

        self.assertEqual(self.client.get(report_url).status_code, 200)
        response = self.client.post(settings_url, {"action": "toggle_sharing"})
        self.assertRedirects(response, report_url)
        self.organization.refresh_from_db()
        self.assertFalse(self.organization.crawler_analytics_shared)

        self.client.post(settings_url, {"action": "toggle_collection"})
        self.organization.refresh_from_db()
        self.assertTrue(self.organization.crawler_analytics_enabled)

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

        visible_nav = self.client.get(reverse("dashboard:home"))
        self.assertContains(visible_nav, reverse("dashboard:crawler-analytics-reports"))
        self.assertContains(visible_nav, "account-nav-menu-compact")
        response = self.client.get(reverse("dashboard:crawler-analytics-reports"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Shared Analytics Co")
        self.assertNotContains(response, "Private Analytics Co")
        self.assertContains(response, "3")
        self.assertContains(response, "table-fixed")
        self.assertContains(response, "sm:min-w-[32rem]")

    def test_client_cannot_access_administrative_report_controls(self):
        self.client.force_login(self.owner)
        response = self.client.get(
            reverse("dashboard:crawler-analytics-admin", args=[self.organization.pk])
        )

        self.assertRedirects(response, reverse("dashboard:home"))
