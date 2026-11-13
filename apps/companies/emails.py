from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse

from .crawler_analytics import build_crawler_analytics_report


class CrawlerAnalyticsNotificationError(Exception):
    pass


def send_crawler_analytics_notification(organization, *, is_shared):
    recipient = (organization.owner.email or "").strip()
    if not recipient:
        raise CrawlerAnalyticsNotificationError("The organization owner has no email address.")

    language_code = "pl" if organization.owner.preferred_language.startswith("pl") else "en"
    report = build_crawler_analytics_report(organization)
    context = {
        "organization": organization,
        "is_shared": is_shared,
        "language_code": language_code,
        "reports_url": settings.SITE_BASE_URL + reverse("dashboard:crawler-analytics-reports"),
        "report": report,
        "top_bot_rows": report["bot_rows"][:5],
    }

    if language_code == "pl":
        subject = (
            f"Raporty AI dla {organization.name} są dostępne"
            if is_shared
            else f"Udostępnianie raportów dla {organization.name} zostało cofnięte"
        )
    else:
        subject = (
            f"AI reports for {organization.name} are available"
            if is_shared
            else f"Report sharing for {organization.name} has been revoked"
        )

    message = EmailMultiAlternatives(
        subject=subject,
        body=render_to_string("emails/crawler_analytics_notification.txt", context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
    )
    message.attach_alternative(
        render_to_string("emails/crawler_analytics_notification.html", context),
        "text/html",
    )
    if message.send() != 1:
        raise CrawlerAnalyticsNotificationError("The crawler analytics email was not accepted for delivery.")
