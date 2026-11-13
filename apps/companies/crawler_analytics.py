import hashlib
import hmac
import ipaddress
import logging
from datetime import timedelta

from django.conf import settings
from django.db.models import Q, Sum
from django.utils import timezone

from .models import (
    CrawlerAnalyticsCategory,
    CrawlerAnalyticsDailyStat,
    CrawlerAnalyticsQueueEvent,
    CrawlerAnalyticsResource,
    Organization,
)


logger = logging.getLogger(__name__)


BOT_REGISTRY = [
    {"pattern": "oai-searchbot", "name": "ChatGPT Search", "category": "ai_search"},
    {"pattern": "chatgpt-user", "name": "ChatGPT User Agent", "category": "ai_search"},
    {"pattern": "perplexitybot", "name": "Perplexity", "category": "ai_search"},
    {"pattern": "perplexity-user", "name": "Perplexity User", "category": "ai_search"},
    {"pattern": "claude-searchbot", "name": "Claude Search", "category": "ai_search"},
    {"pattern": "claude-web", "name": "Claude Web", "category": "ai_search"},
    {"pattern": "youbot", "name": "You.com", "category": "ai_search"},
    {"pattern": "gptbot", "name": "OpenAI GPTBot", "category": "ai_training"},
    {"pattern": "openai", "name": "OpenAIbot", "category": "ai_training"},
    {"pattern": "claudebot", "name": "Anthropic ClaudeBot", "category": "ai_training"},
    {"pattern": "google-extended", "name": "Google Gemini/Extended", "category": "ai_training"},
    {"pattern": "applebot-extended", "name": "Apple Intelligence", "category": "ai_training"},
    {"pattern": "meta-externalagent", "name": "Meta AI", "category": "ai_training"},
    {"pattern": "bytespider", "name": "ByteDance AI", "category": "ai_training"},
    {"pattern": "ccbot", "name": "Common Crawl", "category": "ai_training"},
    {"pattern": "googlebot", "name": "Googlebot", "category": "traditional_seo"},
    {"pattern": "bingbot", "name": "Bingbot", "category": "traditional_seo"},
]
SORTED_BOT_REGISTRY = sorted(BOT_REGISTRY, key=lambda item: len(item["pattern"]), reverse=True)

RESOURCE_ROUTE_NAMES = {
    "public-company-json": CrawlerAnalyticsResource.COMPANY_JSON,
    "public-company-language-json": CrawlerAnalyticsResource.COMPANY_JSON,
    "public-company-jsonld": CrawlerAnalyticsResource.COMPANY_JSONLD,
    "public-company-language-jsonld": CrawlerAnalyticsResource.COMPANY_JSONLD,
    "public-company-md": CrawlerAnalyticsResource.COMPANY_MD,
    "public-company-language-md": CrawlerAnalyticsResource.COMPANY_MD,
    "public-company-llms": CrawlerAnalyticsResource.LLMS_TXT,
    "public-company-language-llms": CrawlerAnalyticsResource.LLMS_TXT,
}

CATEGORY_LABELS = dict(CrawlerAnalyticsCategory.choices)
RESOURCE_LABELS = dict(CrawlerAnalyticsResource.choices)


def identify_crawler(user_agent):
    normalized_user_agent = (user_agent or "").casefold()
    for bot in SORTED_BOT_REGISTRY:
        if bot["pattern"] in normalized_user_agent:
            return bot
    return None


def enqueue_crawler_request(request, organization, resource):
    if not organization.crawler_analytics_enabled or request.method != "GET":
        return False

    bot = identify_crawler(request.META.get("HTTP_USER_AGENT", ""))
    if not bot:
        return False

    remote_address = request.META.get("REMOTE_ADDR", "").strip()
    if not remote_address:
        return False
    try:
        normalized_address = str(ipaddress.ip_address(remote_address))
    except ValueError:
        logger.warning("Skipping crawler analytics because REMOTE_ADDR is not a valid IP address.")
        return False

    ip_hash = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        normalized_address.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    observed_minute = timezone.now().replace(second=0, microsecond=0)
    CrawlerAnalyticsQueueEvent.objects.bulk_create(
        [
            CrawlerAnalyticsQueueEvent(
                organization=organization,
                observed_minute=observed_minute,
                ip_hash=ip_hash,
                bot_key=bot["pattern"],
                bot_name=bot["name"],
                bot_category=bot["category"],
                resource=resource,
            )
        ],
        ignore_conflicts=True,
    )
    return True


def build_crawler_analytics_report(organization):
    today = timezone.localdate()
    start_7_days = today - timedelta(days=6)
    start_30_days = today - timedelta(days=29)
    start_365_days = today - timedelta(days=364)
    stats = CrawlerAnalyticsDailyStat.objects.filter(organization=organization)
    totals = stats.aggregate(
        last_7_days=Sum("request_count", filter=Q(date__range=(start_7_days, today))),
        last_30_days=Sum("request_count", filter=Q(date__range=(start_30_days, today))),
        last_365_days=Sum("request_count", filter=Q(date__range=(start_365_days, today))),
        all_time=Sum("request_count"),
    )
    totals = {key: value or 0 for key, value in totals.items()}

    last_30_days_by_resource = {
        row["resource"]: row["total"]
        for row in stats.filter(date__range=(start_30_days, today))
        .values("resource")
        .annotate(total=Sum("request_count"))
    }
    max_resource_total = max(last_30_days_by_resource.values(), default=0)
    resource_rows = [
        {
            "key": resource,
            "label": label,
            "total": total,
            "percent": round(total * 100 / max_resource_total) if max_resource_total else 0,
        }
        for resource, label in CrawlerAnalyticsResource.choices
        for total in [last_30_days_by_resource.get(resource, 0)]
    ]

    bot_rows = [
        {
            **row,
            "category_label": CATEGORY_LABELS[row["bot_category"]],
            "resource_label": RESOURCE_LABELS[row["resource"]],
        }
        for row in stats.values("bot_name", "bot_category", "resource")
        .annotate(total=Sum("request_count"))
        .order_by("-total", "bot_name", "resource")
    ]
    return {
        **totals,
        "has_data": bool(totals["all_time"]),
        "resource_rows": resource_rows,
        "bot_rows": bot_rows,
    }
