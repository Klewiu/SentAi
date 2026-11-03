"""Validate Stripe prices and keep a local catalog used by checkout."""
from datetime import timedelta
import uuid

import stripe

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

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
    if not settings.INTERNATIONAL_BILLING_ENABLED and currency != BillingCurrency.PLN:
        raise ValueError("EUR billing is currently disabled. Add a PLN price.")
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

    now = timezone.now()
    BillingPlanPrice.objects.select_for_update().filter(
        tier=tier,
        currency=currency,
        active_for_new_customers=True,
    ).exclude(stripe_price_id=price_id).update(active_for_new_customers=False, effective_until=now)

    local, _ = BillingPlanPrice.objects.update_or_create(
        stripe_price_id=price_id,
        defaults={
            "tier": tier,
            "amount": amount,
            "currency": currency,
            "interval": "year",
            "active_for_new_customers": True,
            "effective_from": now,
            "effective_until": None,
            "created_by": actor,
        },
    )
    return local


@transaction.atomic
def set_public_plan_price(tier, amount, actor=None, notes=""):
    """Replace the public PLN price while preserving an auditable price history."""
    if tier not in UserPlanTier.values:
        raise ValueError("Unsupported plan.")
    if not isinstance(amount, int) or amount < 200:
        raise ValueError("Cena brutto musi wynosić co najmniej 2,00 PLN.")

    now = timezone.now()
    cutoff = now - timedelta(days=30)
    prices = BillingPlanPrice.objects.select_for_update().filter(tier=tier, currency=BillingCurrency.PLN)
    current = prices.filter(active_for_new_customers=True).first()
    if current and current.amount == amount:
        raise ValueError("Nowa cena musi różnić się od aktualnej ceny.")

    applied_recently = prices.filter(effective_from__lt=now).filter(
        Q(effective_until__isnull=True) | Q(effective_until__gt=cutoff)
    )
    recent_amounts = list(applied_recently.values_list("amount", flat=True))
    lowest_recent = min(recent_amounts) if recent_amounts else (current.amount if current else None)

    if current:
        current.active_for_new_customers = False
        current.effective_until = now
        current.save(update_fields=["active_for_new_customers", "effective_until", "updated_at"])

    # A crossed-out-price promotion is shown only when the new amount is also
    # below the statutory reference price. This avoids a misleading "reduction"
    # where the new amount is still above the lowest price from the prior 30 days.
    is_reduction = bool(
        current
        and amount < current.amount
        and (lowest_recent is None or amount < lowest_recent)
    )
    return BillingPlanPrice.objects.create(
        tier=tier,
        stripe_price_id=f"local_{uuid.uuid4().hex}",
        amount=amount,
        currency=BillingCurrency.PLN,
        interval="year",
        active_for_new_customers=True,
        effective_from=now,
        previous_amount=current.amount if is_reduction else None,
        lowest_amount_30_days=lowest_recent if is_reduction else None,
        reduction_display_until=now + timedelta(days=30) if is_reduction else None,
        notes=(notes or "").strip(),
        created_by=actor,
    )


@transaction.atomic
def archive_price(local_price):
    local_price = BillingPlanPrice.objects.select_for_update().get(pk=local_price.pk)
    local_price.active_for_new_customers = False
    local_price.effective_until = timezone.now()
    local_price.save(update_fields=["active_for_new_customers", "effective_until", "updated_at"])
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
    now = timezone.now()
    BillingPlanPrice.objects.filter(
        tier=local_price.tier,
        currency=local_price.currency,
        active_for_new_customers=True,
    ).exclude(pk=local_price.pk).update(active_for_new_customers=False, effective_until=now)
    local_price.active_for_new_customers = True
    local_price.effective_from = now
    local_price.effective_until = None
    local_price.save(update_fields=["active_for_new_customers", "effective_from", "effective_until", "updated_at"])
    return local_price
