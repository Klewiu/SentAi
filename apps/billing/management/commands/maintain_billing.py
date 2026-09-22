import time
import stripe
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from apps.accounts.models import User, AuthRateWindow
from apps.billing.access import reconcile_access
from apps.billing.models import BillingSubscription
from apps.billing.services import sync_subscription_from_stripe
from apps.notifications.services import scan_admin_notifications, scan_customer_notifications


class Command(BaseCommand):
    help = "Reconcile subscriptions and generate/resolve notifications. Schedule every 15 minutes."

    def handle(self, *args, **options):
        failures = 0
        if settings.STRIPE_SECRET_KEY:
            stripe.api_key = settings.STRIPE_SECRET_KEY
            from apps.billing.catalog import sync_prices
            try:
                sync_prices()
            except (ValueError, stripe.StripeError):
                failures += 1
                self.stderr.write("Price synchronization failed; cached prices retained.")
            for subscription in BillingSubscription.objects.exclude(stripe_subscription_id="").iterator(chunk_size=100):
                try:
                    sync_subscription_from_stripe(stripe.Subscription.retrieve(subscription.stripe_subscription_id))
                except Exception:
                    failures += 1
                    self.stderr.write(f"Subscription {subscription.pk}: reconciliation failed; retry required.")
        for user in User.objects.filter(is_active=True).iterator(chunk_size=100):
            reconcile_access(user)
            if not user.is_superuser:
                scan_customer_notifications(user)
        scan_admin_notifications()
        AuthRateWindow.objects.filter(window__lt=int(time.time()) // 900 - 1).delete()
        self.stdout.write(f"Reconciliation complete; {failures} synchronization failures.")
        if failures:
            raise CommandError("Billing reconciliation requires retry; inspect Stripe connectivity and price mappings.")
