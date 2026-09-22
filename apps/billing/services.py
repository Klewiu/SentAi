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
    major = amount / 100
    value = f"{int(major)}" if amount % 100 == 0 else f"{major:.2f}"
    return f"{value} {currency.upper()}"


def basic_price_amount(currency: str) -> int:
    return settings.STRIPE_BASIC_PRICE_AMOUNT_EUR if currency == BillingCurrency.EUR else settings.STRIPE_BASIC_PRICE_AMOUNT_PLN


def basic_price_id(currency: str) -> str:
    return settings.STRIPE_BASIC_PRICE_ID_EUR if currency == BillingCurrency.EUR else settings.STRIPE_BASIC_PRICE_ID_PLN


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
        if tier == UserPlanTier.BASIC and (price.amount != basic_price_amount(currency) or price.interval != "year"):
            return None
        return price

    if tier != UserPlanTier.BASIC and currency != normalize_billing_currency(settings.STRIPE_CURRENCY):
        return None

    env_price_id = {
        UserPlanTier.BASIC: basic_price_id(currency),
        UserPlanTier.PLUS: settings.STRIPE_PLUS_PRICE_ID,
        UserPlanTier.PRO: settings.STRIPE_PRO_PRICE_ID,
    }.get(tier, "")
    if not env_price_id:
        return None

    amount = {
        UserPlanTier.BASIC: basic_price_amount(currency),
        UserPlanTier.PLUS: settings.STRIPE_PLUS_PRICE_AMOUNT,
        UserPlanTier.PRO: settings.STRIPE_PRO_PRICE_AMOUNT,
    }[tier]
    price, created = BillingPlanPrice.objects.get_or_create(
        stripe_price_id=env_price_id,
        defaults={
            "tier": tier,
            "amount": amount,
            "currency": currency,
            "active_for_new_customers": True,
        },
    )
    if (created or price.active_for_new_customers) and price.tier == tier and price.currency == currency:
        return price
    return None


def plan_price_label(tier: str, fallback_amount: int | None = None, currency: str | None = None) -> str:
    currency = normalize_billing_currency(currency)
    price = get_active_plan_price(tier, currency)
    if price:
        return price.formatted_amount()
    env_price_id = {
        UserPlanTier.BASIC: basic_price_id(currency),
        UserPlanTier.PLUS: settings.STRIPE_PLUS_PRICE_ID,
        UserPlanTier.PRO: settings.STRIPE_PRO_PRICE_ID,
    }.get(tier, "")
    if not env_price_id or fallback_amount is None or currency != normalize_billing_currency(settings.STRIPE_CURRENCY):
        return ""
    return format_amount(fallback_amount, currency)


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
    if tier in UserPlanTier.values:
        return tier
    return ""


def _user_from_metadata(metadata: Any):
    user_id = object_get(metadata or {}, "user_id")
    if not user_id:
        return None
    return get_user_model().objects.filter(pk=user_id).first()


@transaction.atomic
def sync_subscription_from_stripe(subscription: Any, fallback_user=None, fallback_tier: str = ""):
    from .models import BillingSubscriptionStatus
    subscription_id = object_get(subscription, "id", "")
    status = object_get(subscription, "status", "")
    if not subscription_id.startswith("sub_") or status not in BillingSubscriptionStatus.values:
        raise ValueError("Expected a Stripe subscription")
    metadata = object_get(subscription, "metadata", {}) or {}
    existing = BillingSubscription.objects.filter(stripe_subscription_id=subscription_id).first()
    user = existing.user if existing else (_user_from_metadata(metadata) or fallback_user)
    if not user:
        raise ValueError("Subscription has no known owner")
    if fallback_user and fallback_user.pk != user.pk:
        raise ValueError("Subscription owner mismatch")
    user = get_user_model().objects.select_for_update().get(pk=user.pk)
    current = BillingSubscription.objects.filter(user=user).first()
    customer_id = stripe_id(object_get(subscription, "customer", ""))
    if current and current.stripe_customer_id and current.stripe_customer_id != customer_id:
        raise ValueError("Stripe customer mismatch")
    if current and current.stripe_subscription_id and current.stripe_subscription_id != subscription_id:
        if current.status not in {"canceled", "unpaid", "incomplete_expired"} or status in {"canceled", "unpaid", "incomplete_expired"}:
            raise ValueError("Unexpected subscription replacement")
    items = object_get(subscription, "items", {}) or {}
    item_data = object_get(items, "data", []) or []
    first_item = item_data[0] if item_data else {}
    stripe_price = object_get(first_item, "price", {}) or {}
    stripe_price_id = object_get(stripe_price, "id", "") or ""
    plan_price = BillingPlanPrice.objects.filter(stripe_price_id=stripe_price_id).first()
    if status in paid_access_statuses() and (len(item_data) != 1 or not plan_price):
        raise ValueError("Unknown or unsupported subscription price")
    tier = plan_price.tier if plan_price else (current.tier if current else UserPlanTier.BASIC)
    current_period_start = object_get(subscription, "current_period_start") or object_get(first_item, "current_period_start")
    current_period_end = object_get(subscription, "current_period_end") or object_get(first_item, "current_period_end")

    billing_subscription, _ = BillingSubscription.objects.update_or_create(
        user=user,
        defaults={
            "tier": tier,
            "plan_price": plan_price,
            "stripe_customer_id": customer_id,
            "stripe_subscription_id": object_get(subscription, "id", "") or "",
            "stripe_price_id": stripe_price_id,
            "status": object_get(subscription, "status", "") or "incomplete",
            "current_period_start": stripe_timestamp_to_datetime(current_period_start),
            "current_period_end": stripe_timestamp_to_datetime(current_period_end),
            "cancel_at_period_end": bool(object_get(subscription, "cancel_at_period_end", False)),
            "canceled_at": stripe_timestamp_to_datetime(object_get(subscription, "canceled_at")),
            "latest_invoice_id": stripe_id(object_get(subscription, "latest_invoice", "")),
        },
    )

    from .access import reconcile_access
    reconcile_access(user)

    return billing_subscription


@transaction.atomic
def record_invoice_payment(invoice: Any):
    subscription_id = stripe_id(object_get(invoice, "subscription", ""))
    if not subscription_id:
        parent = object_get(invoice, "parent", {}) or {}
        subscription_id = stripe_id(object_get(object_get(parent, "subscription_details", {}) or {}, "subscription", ""))
    customer_id = stripe_id(object_get(invoice, "customer", ""))
    billing_subscription = None
    if subscription_id:
        billing_subscription = BillingSubscription.objects.filter(stripe_subscription_id=subscription_id).first()
    if not billing_subscription and not subscription_id and customer_id:
        billing_subscription = BillingSubscription.objects.filter(stripe_customer_id=customer_id).first()
    if not billing_subscription:
        raise ValueError("Invoice subscription has not been synchronized")
    if customer_id != billing_subscription.stripe_customer_id:
        raise ValueError("Invoice customer mismatch")
    if not stripe_id(object_get(invoice, "id", "")).startswith("in_"):
        raise ValueError("Missing invoice ID")

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


def stripe_id(value):
    return value if isinstance(value, str) else object_get(value, "id", "") or ""


@transaction.atomic
def upgrade_subscription(user, plan_price):
    import stripe
    user = get_user_model().objects.select_for_update().get(pk=user.pk)
    billing = BillingSubscription.objects.get(user=user)
    stripe.api_key = settings.STRIPE_SECRET_KEY
    subscription = stripe.Subscription.retrieve(billing.stripe_subscription_id)
    if stripe_id(object_get(subscription, "customer")) != billing.stripe_customer_id:
        raise ValueError("Subscription customer mismatch")
    items = object_get(object_get(subscription, "items", {}), "data", [])
    if len(items) != 1 or plan_price.tier not in {UserPlanTier.PLUS, UserPlanTier.PRO}:
        raise ValueError("Unsupported upgrade")
    current_price = stripe_id(object_get(items[0], "price"))
    if current_price == plan_price.stripe_price_id:
        sync_subscription_from_stripe(subscription, fallback_user=user)
        return ""
    current_plan = BillingPlanPrice.objects.filter(stripe_price_id=current_price).first()
    ranks = {UserPlanTier.BASIC: 0, UserPlanTier.PLUS: 1, UserPlanTier.PRO: 2}
    if not current_plan or ranks[plan_price.tier] <= ranks[current_plan.tier] or current_plan.currency != plan_price.currency:
        raise ValueError("Only upgrades in the same currency are supported")
    if object_get(subscription, "status") != "active" or object_get(subscription, "cancel_at_period_end", False):
        raise ValueError("Reactivate and settle your subscription before upgrading")
    if not object_get(subscription, "pending_update"):
        # One subscription invoice, no separate Checkout payment. Keep the existing
        # full annual charge policy; apply the new price only when payment succeeds.
        anchor = object_get(subscription, "current_period_start") or object_get(items[0], "current_period_start", "")
        subscription = stripe.Subscription.modify(
            billing.stripe_subscription_id,
            items=[{"id": stripe_id(items[0]), "price": plan_price.stripe_price_id}],
            billing_cycle_anchor="now", proration_behavior="none",
            payment_behavior="pending_if_incomplete",
            idempotency_key=f"upgrade:{billing.stripe_subscription_id}:{anchor}:{plan_price.stripe_price_id}",
        )
    sync_subscription_from_stripe(subscription, fallback_user=user)
    invoice_id = stripe_id(object_get(subscription, "latest_invoice"))
    if invoice_id:
        invoice = stripe.Invoice.retrieve(invoice_id)
        record_invoice_payment(invoice)
        return object_get(invoice, "hosted_invoice_url", "") or ""
    return ""
