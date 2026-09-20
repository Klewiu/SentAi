"""Provision the fixed Basic annual price in the configured Stripe account."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from apps.billing.models import BillingPlanPrice


class Command(BaseCommand):
    help = "Create/reuse the Basic 100 PLN and 25 EUR annual Stripe prices."

    def handle(self, *args, **options):
        import stripe
        if not settings.STRIPE_SECRET_KEY:
            raise CommandError("Stripe is not configured.")
        stripe.api_key = settings.STRIPE_SECRET_KEY
        try:
            for currency, amount, lookup_key in (
                ("pln", settings.STRIPE_BASIC_PRICE_AMOUNT_PLN, "xoaila_basic_pln_year_100"),
                ("eur", settings.STRIPE_BASIC_PRICE_AMOUNT_EUR, "xoaila_basic_eur_year_25"),
            ):
                prices = stripe.Price.list(lookup_keys=[lookup_key], limit=1)
                if prices.data:
                    price = prices.data[0]
                else:
                    price = stripe.Price.create(
                        currency=currency,
                        unit_amount=amount,
                        recurring={"interval": "year"},
                        product_data={"name": "xoaila Basic"},
                        lookup_key=lookup_key,
                        idempotency_key=f"xoaila-basic-{currency}-year-{amount}-v1",
                    )
                if (
                    price.unit_amount != amount
                    or price.currency != currency
                    or not price.active
                    or price.recurring.interval != "year"
                    or price.recurring.interval_count != 1
                ):
                    raise CommandError(f"Existing Basic {currency.upper()} Stripe price has incompatible terms.")
                with transaction.atomic():
                    BillingPlanPrice.objects.filter(
                        tier="BASIC", currency=currency, active_for_new_customers=True
                    ).exclude(stripe_price_id=price.id).update(active_for_new_customers=False)
                    BillingPlanPrice.objects.update_or_create(
                        stripe_price_id=price.id,
                        defaults={
                            "tier": "BASIC",
                            "amount": amount,
                            "currency": currency,
                            "interval": "year",
                            "active_for_new_customers": True,
                        },
                    )
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Basic {amount / 100:g} {currency.upper()}/year configured in Stripe "
                        + ("live" if price.livemode else "test")
                        + " mode."
                    )
                )
        except stripe.StripeError:
            raise CommandError("Stripe configuration failed. Check credentials and connectivity.") from None
