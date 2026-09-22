from django.core.management.base import BaseCommand, CommandError
import stripe
from apps.billing.catalog import sync_prices, link_existing_prices


class Command(BaseCommand):
    help = "Import current xoaila annual offers from Stripe without sharing customer databases."

    def add_arguments(self, parser):
        parser.add_argument("--link-existing", action="store_true", help="One-time: attach stable lookup keys to existing locally configured Stripe prices.")

    def handle(self, *args, **options):
        try:
            count = link_existing_prices() if options["link_existing"] else sync_prices()
        except (ValueError, stripe.StripeError) as error:
            raise CommandError(str(error) if isinstance(error, ValueError) else "Stripe connection failed.") from None
        self.stdout.write(self.style.SUCCESS(f"Active prices synchronized: {count}/6"))
