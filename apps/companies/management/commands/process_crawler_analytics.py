from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.companies.models import CrawlerAnalyticsDailyStat, CrawlerAnalyticsQueueEvent


class Command(BaseCommand):
    help = "Aggregate queued crawler requests into daily report statistics."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=2000)

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        if batch_size < 1:
            raise CommandError("--batch-size must be greater than zero.")

        with transaction.atomic():
            events = list(
                CrawlerAnalyticsQueueEvent.objects.select_for_update()
                .order_by("pk")
                .values(
                    "pk",
                    "organization_id",
                    "observed_minute",
                    "bot_key",
                    "bot_name",
                    "bot_category",
                    "resource",
                )[:batch_size]
            )
            if not events:
                self.stdout.write("No crawler analytics events to process.")
                return

            grouped = defaultdict(int)
            for event in events:
                report_date = timezone.localtime(event["observed_minute"]).date()
                key = (
                    event["organization_id"],
                    report_date,
                    event["bot_key"],
                    event["bot_name"],
                    event["bot_category"],
                    event["resource"],
                )
                grouped[key] += 1

            for key, count in grouped.items():
                organization_id, report_date, bot_key, bot_name, bot_category, resource = key
                stat, created = CrawlerAnalyticsDailyStat.objects.get_or_create(
                    organization_id=organization_id,
                    date=report_date,
                    bot_key=bot_key,
                    resource=resource,
                    defaults={
                        "bot_name": bot_name,
                        "bot_category": bot_category,
                        "request_count": count,
                    },
                )
                if not created:
                    CrawlerAnalyticsDailyStat.objects.filter(pk=stat.pk).update(
                        request_count=F("request_count") + count,
                    )

            CrawlerAnalyticsQueueEvent.objects.filter(
                pk__in=[event["pk"] for event in events]
            ).delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"Processed {len(events)} crawler events into {len(grouped)} daily buckets."
            )
        )
