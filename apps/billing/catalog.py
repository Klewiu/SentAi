"""Stripe owns the offer; local rows cache it and preserve subscription history."""
import hashlib
import stripe
from django.conf import settings
from django.db import transaction
from apps.accounts.models import UserPlanTier
from .models import BillingCurrency, BillingPlanPrice
from .services import object_get


def configure():
    if not settings.STRIPE_SECRET_KEY:
        raise ValueError("Configure STRIPE_SECRET_KEY before synchronizing or changing prices.")
    stripe.api_key = settings.STRIPE_SECRET_KEY


def lookup_key(tier, currency):
    if tier not in UserPlanTier.values or currency not in BillingCurrency.values:
        raise ValueError("Unsupported plan or currency.")
    return f"xoaila_{tier.lower()}_{currency}_year"


def validate(price, currency):
    recurring = object_get(price, "recurring", {}) or {}
    amount = object_get(price, "unit_amount")
    if (not object_get(price, "id", "").startswith("price_")
        or object_get(price, "currency") != currency
        or not isinstance(amount, int) or amount <= 0
        or object_get(recurring, "interval") != "year"
        or object_get(recurring, "interval_count", 1) != 1
        or object_get(recurring, "usage_type", "licensed") != "licensed"
        or object_get(price, "billing_scheme", "per_unit") != "per_unit"
        or object_get(price, "transform_quantity")):
        raise ValueError("Stripe price must have a positive fixed amount and annual licensed billing in the selected currency.")


def remote_catalog():
    keys = {lookup_key(t, c): (t, c) for t in UserPlanTier.values for c in BillingCurrency.values}
    result = stripe.Price.list(lookup_keys=list(keys), limit=100)
    rows = []
    for price in result.auto_paging_iter():
        key = object_get(price, "lookup_key")
        if key not in keys:
            continue
        tier, currency = keys[key]
        validate(price, currency)
        rows.append((tier, currency, price))
    return rows


@transaction.atomic
def cache_catalog(rows, actor=None):
    # Validate the complete response before altering any local offer.
    for tier, currency, price in rows:
        validate(price, currency)
        existing = BillingPlanPrice.objects.filter(stripe_price_id=price.id).first()
        if existing and (existing.tier != tier or existing.currency != currency):
            raise ValueError("Stripe price is already assigned to a different plan locally.")
    BillingPlanPrice.objects.filter(active_for_new_customers=True).update(active_for_new_customers=False)
    for tier, currency, price in rows:
        defaults = dict(tier=tier, currency=currency, amount=price.unit_amount,
                        interval="year", active_for_new_customers=bool(price.active))
        if actor:
            defaults["created_by"] = actor
        BillingPlanPrice.objects.update_or_create(stripe_price_id=price.id, defaults=defaults)
    return sum(bool(price.active) for _, _, price in rows)


def sync_prices(actor=None):
    configure()
    rows = remote_catalog()
    if not rows:
        raise ValueError("No xoaila lookup keys found in this Stripe account. Link existing prices or create an offer first.")
    return cache_catalog(rows, actor)


def publish_price(tier, currency, amount, actor=None, existing_id=""):
    configure()
    key = lookup_key(tier, currency)
    current = next((p for t, c, p in remote_catalog() if (t, c) == (tier, currency)), None)
    if existing_id:
        price = stripe.Price.retrieve(existing_id)
        validate(price, currency)
        if price.unit_amount != amount:
            raise ValueError("Amount differs from Stripe. Clear the existing price ID to create a new price.")
        if price.lookup_key and price.lookup_key != key and price.lookup_key.startswith("xoaila_") and price.lookup_key not in {"xoaila_basic_pln_year_100", "xoaila_basic_eur_year_25"}:
            raise ValueError("This Stripe price belongs to a different offer.")
        stripe.Price.modify(price.id, lookup_key=key, transfer_lookup_key=True, active=True)
    elif current and current.unit_amount == amount and current.active:
        pass  # A retry after a lost response must not create another price.
    else:
        if not isinstance(amount, int) or amount <= 0:
            raise ValueError("Amount must be positive.")
        args = dict(currency=currency, unit_amount=amount, recurring={"interval": "year"},
                    lookup_key=key, transfer_lookup_key=True,
                    metadata={"app": "xoaila", "plan_tier": tier})
        if current:
            args["product"] = object_get(current, "product")
            args["tax_behavior"] = object_get(current, "tax_behavior", "unspecified")
        else:
            args["product_data"] = {"name": f"xoaila {tier.title()}"}
        token = hashlib.sha256(f"{key}:{current.id if current else 'initial'}:{amount}".encode()).hexdigest()
        stripe.Price.create(**args, idempotency_key=f"xoaila-price-{token}")
    # Never update subscription items: existing subscribers retain their price.
    return sync_prices(actor)


def set_price_active(local_price, active, actor=None):
    configure()
    key = lookup_key(local_price.tier, local_price.currency)
    price = stripe.Price.retrieve(local_price.stripe_price_id)
    validate(price, local_price.currency)
    if active:
        stripe.Price.modify(price.id, active=True, lookup_key=key, transfer_lookup_key=True)
    else:
        stripe.Price.modify(price.id, active=False)
    return sync_prices(actor)


def link_existing_prices():
    """One-time adoption of explicitly configured local prices; never change amounts."""
    configure()
    current = {(t, c) for t, c, _ in remote_catalog()}
    candidates = []
    for local in BillingPlanPrice.objects.filter(active_for_new_customers=True):
        if (local.tier, local.currency) in current:
            continue
        remote = stripe.Price.retrieve(local.stripe_price_id)
        validate(remote, local.currency)
        if remote.unit_amount != local.amount or not remote.active:
            raise ValueError("Local and Stripe prices differ. Correct the mapping before linking.")
        candidates.append(local)
    for local in candidates:
        publish_price(local.tier, local.currency, local.amount, existing_id=local.stripe_price_id)
    return sync_prices()
