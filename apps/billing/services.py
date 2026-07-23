from __future__ import annotations

from datetime import datetime, timezone as datetime_timezone
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import UserPlanTier
from apps.subscriptions.models import Subscription

from .models import BillingCurrency, BillingPayment, BillingPaymentStatus, BillingPlanPrice, BillingSubscription


def format_amount(amount: int, currency: str) -> str:
    return f"{amount / 100:.2f} {currency.upper()}"


def stripe_timestamp_to_datetime(value: Any):
    if not value:
        return None
    return datetime.fromtimestamp(int(value), tz=datetime_timezone.utc)


def object_get(obj: Any, key: str, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def normalize_billing_currency(currency: str | None = None) -> str:
    value = (currency or settings.STRIPE_CURRENCY or BillingCurrency.PLN).strip().lower()
    if value in BillingCurrency.values:
        return value
    return settings.STRIPE_CURRENCY if settings.STRIPE_CURRENCY in BillingCurrency.values else BillingCurrency.PLN


def supported_billing_currencies() -> list[str]:
    return list(BillingCurrency.values)


def get_active_plan_price(tier: str, currency: str | None = None) -> BillingPlanPrice | None:
    currency = normalize_billing_currency(currency)
    price = BillingPlanPrice.objects.filter(
        tier=tier,
        currency=currency,
        active_for_new_customers=True,
    ).first()
    if price:
        return price

    if currency != normalize_billing_currency(settings.STRIPE_CURRENCY):
        return None

    env_price_id = {
        UserPlanTier.PLUS: settings.STRIPE_PLUS_PRICE_ID,
        UserPlanTier.PRO: settings.STRIPE_PRO_PRICE_ID,
    }.get(tier, "")
    if not env_price_id:
        return None

    amount = {
        UserPlanTier.PLUS: settings.STRIPE_PLUS_PRICE_AMOUNT,
        UserPlanTier.PRO: settings.STRIPE_PRO_PRICE_AMOUNT,
    }[tier]
    price, created = BillingPlanPrice.objects.get_or_create(
        stripe_price_id=env_price_id,
        defaults={
            "tier": tier,
            "amount": amount,
            "currency": settings.STRIPE_CURRENCY,
            "active_for_new_customers": True,
        },
    )
    if created or price.active_for_new_customers:
        return price
    return None


def plan_price_label(tier: str, fallback_amount: int | None = None, currency: str | None = None) -> str:
    currency = normalize_billing_currency(currency)
    price = get_active_plan_price(tier, currency)
    if price:
        return price.formatted_amount()
    env_price_id = {
        UserPlanTier.PLUS: settings.STRIPE_PLUS_PRICE_ID,
        UserPlanTier.PRO: settings.STRIPE_PRO_PRICE_ID,
    }.get(tier, "")
    if not env_price_id or fallback_amount is None or currency != normalize_billing_currency(settings.STRIPE_CURRENCY):
        return ""
    return format_amount(fallback_amount, settings.STRIPE_CURRENCY)


def paid_access_statuses() -> set[str]:
    return {"active", "trialing"}


@transaction.atomic
def activate_paid_plan(user, tier: str, billing_subscription: BillingSubscription | None = None):
    user.plan_tier = tier
    if user.paid_plan_started_at is None:
        user.paid_plan_started_at = timezone.now()
    if user.plan_selected_at is None:
        user.plan_selected_at = timezone.now()
    user.save(update_fields=["plan_tier", "paid_plan_started_at", "plan_selected_at"])
    Subscription.objects.filter(organization__owner=user).update(tier=tier)

    if billing_subscription and billing_subscription.status in paid_access_statuses():
        billing_subscription.tier = tier
        billing_subscription.save(update_fields=["tier", "updated_at"])


@transaction.atomic
def downgrade_to_basic(user):
    user.plan_tier = UserPlanTier.BASIC
    user.paid_plan_started_at = None
    if user.plan_selected_at is None:
        user.plan_selected_at = timezone.now()
    user.save(update_fields=["plan_tier", "paid_plan_started_at", "plan_selected_at"])
    Subscription.objects.filter(organization__owner=user).update(tier=UserPlanTier.BASIC)


def _tier_from_metadata(metadata: Any) -> str:
    tier = object_get(metadata or {}, "plan_tier", "")
    if tier in {UserPlanTier.PLUS, UserPlanTier.PRO}:
        return tier
    return ""


def _user_from_metadata(metadata: Any):
    user_id = object_get(metadata or {}, "user_id")
    if not user_id:
        return None
    return get_user_model().objects.filter(pk=user_id).first()


@transaction.atomic
def sync_subscription_from_stripe(subscription: Any, fallback_user=None, fallback_tier: str = ""):
    metadata = object_get(subscription, "metadata", {}) or {}
    user = _user_from_metadata(metadata) or fallback_user
    if not user:
        return None

    items = object_get(subscription, "items", {}) or {}
    item_data = object_get(items, "data", []) or []
    first_item = item_data[0] if item_data else {}
    stripe_price = object_get(first_item, "price", {}) or {}
    stripe_price_id = object_get(stripe_price, "id", "") or ""
    plan_price = BillingPlanPrice.objects.filter(stripe_price_id=stripe_price_id).first()
    tier = _tier_from_metadata(metadata) or fallback_tier or (plan_price.tier if plan_price else user.plan_tier)
    current_period_start = object_get(subscription, "current_period_start") or object_get(first_item, "current_period_start")
    current_period_end = object_get(subscription, "current_period_end") or object_get(first_item, "current_period_end")

    billing_subscription, _ = BillingSubscription.objects.update_or_create(
        user=user,
        defaults={
            "tier": tier,
            "plan_price": plan_price,
            "stripe_customer_id": object_get(subscription, "customer", "") or "",
            "stripe_subscription_id": object_get(subscription, "id", "") or "",
            "stripe_price_id": stripe_price_id,
            "status": object_get(subscription, "status", "") or "incomplete",
            "current_period_start": stripe_timestamp_to_datetime(current_period_start),
            "current_period_end": stripe_timestamp_to_datetime(current_period_end),
            "cancel_at_period_end": bool(object_get(subscription, "cancel_at_period_end", False)),
            "canceled_at": stripe_timestamp_to_datetime(object_get(subscription, "canceled_at")),
            "latest_invoice_id": object_get(subscription, "latest_invoice", "") or "",
        },
    )

    if billing_subscription.status in paid_access_statuses() and tier in {UserPlanTier.PLUS, UserPlanTier.PRO}:
        activate_paid_plan(user, tier, billing_subscription)
    elif billing_subscription.status in {"canceled", "unpaid", "incomplete_expired"}:
        downgrade_to_basic(user)

    return billing_subscription


@transaction.atomic
def record_invoice_payment(invoice: Any):
    subscription_id = object_get(invoice, "subscription", "") or ""
    customer_id = object_get(invoice, "customer", "") or ""
    billing_subscription = None
    if subscription_id:
        billing_subscription = BillingSubscription.objects.filter(stripe_subscription_id=subscription_id).first()
    if not billing_subscription and customer_id:
        billing_subscription = BillingSubscription.objects.filter(stripe_customer_id=customer_id).first()
    if not billing_subscription:
        return None

    status = object_get(invoice, "status", "") or BillingPaymentStatus.OPEN
    status_transitions = object_get(invoice, "status_transitions", {}) or {}
    paid_at = stripe_timestamp_to_datetime(object_get(status_transitions, "paid_at"))
    if not paid_at and status == BillingPaymentStatus.PAID:
        paid_at = timezone.now()

    payment, _ = BillingPayment.objects.update_or_create(
        stripe_invoice_id=object_get(invoice, "id", "") or "",
        defaults={
            "user": billing_subscription.user,
            "subscription": billing_subscription,
            "stripe_payment_intent_id": object_get(invoice, "payment_intent", "") or "",
            "amount_paid": int(object_get(invoice, "amount_paid", 0) or 0),
            "currency": object_get(invoice, "currency", settings.STRIPE_CURRENCY) or settings.STRIPE_CURRENCY,
            "status": status if status in BillingPaymentStatus.values else BillingPaymentStatus.OPEN,
            "paid_at": paid_at,
            "hosted_invoice_url": object_get(invoice, "hosted_invoice_url", "") or "",
            "invoice_pdf": object_get(invoice, "invoice_pdf", "") or "",
        },
    )

    if payment.status == BillingPaymentStatus.PAID:
        billing_subscription.latest_invoice_id = payment.stripe_invoice_id
        billing_subscription.latest_payment_at = payment.paid_at or timezone.now()
        billing_subscription.save(update_fields=["latest_invoice_id", "latest_payment_at", "updated_at"])
        from apps.notifications.services import notify_invoice_needed_for_payment

        if not payment.invoices.exists():
            notify_invoice_needed_for_payment(payment)

    return payment
