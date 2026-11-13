from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import UserPlanTier
from apps.billing.models import BillingSubscription
from apps.companies.crawler_analytics import build_crawler_analytics_report, identify_crawler
from apps.companies.models import (
    CrawlerAnalyticsDailyStat,
    CrawlerAnalyticsQueueEvent,
    CrawlerAnalyticsResource,
    Organization,
    VerificationStatus,
)


User = get_user_model()


class CrawlerAnalyticsCollectionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(
            username="crawler-owner",
            email="crawler-owner@example.com",
            password="strong-pass-123",
        )
        self.owner.plan_tier = UserPlanTier.PRO
        self.owner.save(update_fields=["plan_tier"])
        BillingSubscription.objects.create(
            user=self.owner,
            tier=self.owner.plan_tier,
            status="active",
            current_period_end=timezone.now() + timedelta(days=365),
        )
        self.organization = Organization.objects.create(
            owner=self.owner,
            name="Crawler Analytics Co",
            slug="crawler-analytics-co",
            public=True,
            allow_ai_indexing=True,
            verification_status=VerificationStatus.HUMAN_ADMIN_VERIFIED,
        )

    def test_collection_is_disabled_by_default_and_only_known_bots_are_queued(self):
        url = f"/api/public/{self.organization.slug}/company.json"
        response = self.client.get(url, HTTP_USER_AGENT="OAI-SearchBot", REMOTE_ADDR="192.0.2.1")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CrawlerAnalyticsQueueEvent.objects.exists())

        Organization.objects.filter(pk=self.organization.pk).update(crawler_analytics_enabled=True)
        response = self.client.get(url, HTTP_USER_AGENT="Mozilla/5.0", REMOTE_ADDR="192.0.2.1")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(CrawlerAnalyticsQueueEvent.objects.exists())

    def test_events_are_deduplicated_by_organization_ip_bot_file_and_minute(self):
        Organization.objects.filter(pk=self.organization.pk).update(crawler_analytics_enabled=True)
        json_url = f"/api/public/{self.organization.slug}/company.json"
        jsonld_url = f"/api/public/{self.organization.slug}/company.jsonld"

        self.client.get(json_url, HTTP_USER_AGENT="OAI-SearchBot", REMOTE_ADDR="192.0.2.1")
        self.client.get(json_url, HTTP_USER_AGENT="OAI-SearchBot", REMOTE_ADDR="192.0.2.1")
        self.client.get(json_url, HTTP_USER_AGENT="OAI-SearchBot", REMOTE_ADDR="192.0.2.2")
        self.client.get(jsonld_url, HTTP_USER_AGENT="OAI-SearchBot", REMOTE_ADDR="192.0.2.1")

        events = CrawlerAnalyticsQueueEvent.objects.order_by("resource", "ip_hash")
        self.assertEqual(events.count(), 3)
        self.assertNotIn("192.0.2.1", events.first().ip_hash)
        self.assertEqual(
            set(events.values_list("resource", flat=True)),
            {CrawlerAnalyticsResource.COMPANY_JSON, CrawlerAnalyticsResource.COMPANY_JSONLD},
        )

    def test_all_supported_public_files_are_counted_and_failed_or_non_get_requests_are_ignored(self):
        Organization.objects.filter(pk=self.organization.pk).update(crawler_analytics_enabled=True)
        requests = (
            ("company.json", CrawlerAnalyticsResource.COMPANY_JSON),
            ("company.jsonld", CrawlerAnalyticsResource.COMPANY_JSONLD),
            ("company.md", CrawlerAnalyticsResource.COMPANY_MD),
            ("llms.txt", CrawlerAnalyticsResource.LLMS_TXT),
        )
        for index, (filename, resource) in enumerate(requests, start=1):
            response = self.client.get(
                f"/api/public/{self.organization.slug}/{filename}",
                HTTP_USER_AGENT="PerplexityBot",
                REMOTE_ADDR=f"192.0.2.{index}",
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(
                CrawlerAnalyticsQueueEvent.objects.filter(resource=resource).exists()
            )

        self.client.get(
            "/api/public/missing-company/company.json",
            HTTP_USER_AGENT="PerplexityBot",
            REMOTE_ADDR="192.0.2.10",
        )
        self.client.post(
            f"/api/public/{self.organization.slug}/company.json",
            HTTP_USER_AGENT="PerplexityBot",
            REMOTE_ADDR="192.0.2.11",
        )
        self.assertEqual(CrawlerAnalyticsQueueEvent.objects.count(), 4)

    def test_bot_registry_matches_case_insensitively_and_prefers_specific_patterns(self):
        self.assertEqual(identify_crawler("Mozilla OAI-SearchBot/1.0")["name"], "ChatGPT Search")
        self.assertEqual(identify_crawler("OpenAIbot")["name"], "OpenAIbot")
        self.assertIsNone(identify_crawler("Mozilla/5.0"))


class CrawlerAnalyticsAggregationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="aggregation-owner",
            email="aggregation-owner@example.com",
            password="strong-pass-123",
        )
        self.organization = Organization.objects.create(
            owner=self.owner,
            name="Aggregation Co",
            slug="aggregation-co",
        )

    def test_management_command_aggregates_and_removes_queue_events(self):
        observed_minute = timezone.now().replace(second=0, microsecond=0)
        for ip_hash in ("a" * 64, "b" * 64):
            CrawlerAnalyticsQueueEvent.objects.create(
                organization=self.organization,
                observed_minute=observed_minute,
                ip_hash=ip_hash,
                bot_key="oai-searchbot",
                bot_name="ChatGPT Search",
                bot_category="ai_search",
                resource=CrawlerAnalyticsResource.COMPANY_JSON,
            )

        call_command("process_crawler_analytics", batch_size=20)

        stat = CrawlerAnalyticsDailyStat.objects.get(organization=self.organization)
        self.assertEqual(stat.request_count, 2)
        self.assertFalse(CrawlerAnalyticsQueueEvent.objects.exists())
        call_command("process_crawler_analytics", batch_size=20)
        stat.refresh_from_db()
        self.assertEqual(stat.request_count, 2)

    def test_report_counts_the_requested_date_windows_and_resource_rows(self):
        today = timezone.localdate()
        counts_by_age = ((0, 1), (6, 2), (7, 4), (29, 8), (30, 16), (364, 32), (365, 64))
        for age, count in counts_by_age:
            CrawlerAnalyticsDailyStat.objects.create(
                organization=self.organization,
                date=today - timedelta(days=age),
                bot_key="oai-searchbot",
                bot_name="ChatGPT Search",
                bot_category="ai_search",
                resource=CrawlerAnalyticsResource.COMPANY_JSON,
                request_count=count,
            )

        report = build_crawler_analytics_report(self.organization)

        self.assertEqual(report["last_7_days"], 3)
        self.assertEqual(report["last_30_days"], 15)
        self.assertEqual(report["last_365_days"], 63)
        self.assertEqual(report["all_time"], 127)
        self.assertEqual(report["resource_rows"][0]["total"], 15)
