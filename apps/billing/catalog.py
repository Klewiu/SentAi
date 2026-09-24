"""Validate Stripe prices and keep a local catalog used by checkout."""
import stripe

from django.conf import settings
from django.db import transaction

from apps.accounts.models import UserPlanTier

from .models import BillingCurrency, BillingPlanPrice
from .services import object_get


def configure():
    if not settings.STRIPE_SECRET_KEY:
        raise ValueError("Configure STRIPE_SECRET_KEY before importing a price.")
    stripe.api_key = settings.STRIPE_SECRET_KEY


def validate_price_id(price_id):
    value = (price_id or "").strip()
    if value.startswith("prod_"):
        raise ValueError(
            "To jest identyfikator produktu Stripe (prod_...). Wprowadź identyfikator ceny zaczynający się od price_. / "
            "This is a Stripe Product ID (prod_...). Enter a Price ID beginning with price_."
        )
    if not value.startswith("price_"):
        raise ValueError("Stripe Price ID musi zaczynać się od price_. / Stripe Price ID must begin with price_.")
    return value


def validate_remote_price(price):
    recurring = object_get(price, "recurring", {}) or {}
    currency = object_get(price, "currency", "")
    amount = object_get(price, "unit_amount")
    if not object_get(price, "id", "").startswith("price_"):
        raise ValueError("Stripe did not return a valid Price object.")
    if currency not in BillingCurrency.values:
        raise ValueError("Cena musi być w PLN lub EUR. / Price currency must be PLN or EUR.")
    if not isinstance(amount, int) or amount <= 0:
        raise ValueError("Cena Stripe musi mieć stałą dodatnią kwotę. / Stripe price must have a positive fixed amount.")
    if object_get(recurring, "interval") != "year" or object_get(recurring, "interval_count", 1) != 1:
        raise ValueError("Cena musi być roczną subskrypcją. / Price must be an annual subscription.")
    if object_get(recurring, "usage_type", "licensed") != "licensed":
        raise ValueError("Cena musi używać rozliczenia licensed. / Price must use licensed billing.")
    if object_get(price, "billing_scheme", "per_unit") != "per_unit" or object_get(price, "transform_quantity"):
        raise ValueError("Cena musi mieć stałą kwotę za plan. / Price must have a fixed per-plan amount.")
    if not object_get(price, "active", False):
        raise ValueError("Cena Stripe jest nieaktywna. / Stripe price is inactive.")
    return amount, currency


def retrieve_price(price_id):
    configure()
    price_id = validate_price_id(price_id)
    price = stripe.Price.retrieve(price_id)
    validate_remote_price(price)
    return price


def import_price(tier, price_id, actor=None):
    """Import one existing Stripe price without modifying anything in Stripe."""
    if tier not in UserPlanTier.values:
        raise ValueError("Unsupported plan.")
    price = retrieve_price(price_id)
    amount, currency = validate_remote_price(price)
    price_id = object_get(price, "id")
    return _save_imported_price(tier, price_id, amount, currency, actor)


@transaction.atomic
def _save_imported_price(tier, price_id, amount, currency, actor):
    assigned = BillingPlanPrice.objects.select_for_update().filter(stripe_price_id=price_id).first()
    if assigned and assigned.tier != tier:
        raise ValueError("Ta cena jest już przypisana do innego planu. / This price is already assigned to another plan.")

    BillingPlanPrice.objects.select_for_update().filter(
        tier=tier,
        currency=currency,
        active_for_new_customers=True,
    ).exclude(stripe_price_id=price_id).update(active_for_new_customers=False)

    local, _ = BillingPlanPrice.objects.update_or_create(
        stripe_price_id=price_id,
        defaults={
            "tier": tier,
            "amount": amount,
            "currency": currency,
            "interval": "year",
            "active_for_new_customers": True,
            "created_by": actor,
        },
    )
    return local


@transaction.atomic
def archive_price(local_price):
    local_price = BillingPlanPrice.objects.select_for_update().get(pk=local_price.pk)
    local_price.active_for_new_customers = False
    local_price.save(update_fields=["active_for_new_customers", "updated_at"])
    return local_price


def activate_price(local_price):
    """Reactivate locally only after confirming the remote price is still usable."""
    remote = retrieve_price(local_price.stripe_price_id)
    amount, currency = validate_remote_price(remote)
    if amount != local_price.amount or currency != local_price.currency:
        raise ValueError("Lokalne dane ceny nie zgadzają się ze Stripe. / Local price data does not match Stripe.")
    return _activate_local_price(local_price.pk)


@transaction.atomic
def _activate_local_price(price_pk):
    local_price = BillingPlanPrice.objects.select_for_update().get(pk=price_pk)
    BillingPlanPrice.objects.filter(
        tier=local_price.tier,
        currency=local_price.currency,
        active_for_new_customers=True,
    ).exclude(pk=local_price.pk).update(active_for_new_customers=False)
    local_price.active_for_new_customers = True
    local_price.save(update_fields=["active_for_new_customers", "updated_at"])
    return local_price
