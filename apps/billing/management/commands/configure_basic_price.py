import stripe

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import UserPlanTier
from apps.billing.catalog import import_price


class Command(BaseCommand):
    help = "Import an existing annual Stripe Price as the Basic offer."

    def add_arguments(self, parser):
        parser.add_argument("price_id", help="Stripe Price ID beginning with price_ (not Product ID prod_).")

    def handle(self, *args, **options):
        try:
            price = import_price(UserPlanTier.BASIC, options["price_id"])
        except ValueError as error:
            raise CommandError(str(error)) from None
        except stripe.StripeError:
            raise CommandError("Could not retrieve this Price from Stripe.") from None
        self.stdout.write(self.style.SUCCESS(f"Imported Basic: {price.formatted_amount()} ({price.stripe_price_id})"))
