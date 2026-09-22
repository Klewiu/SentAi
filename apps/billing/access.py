"""Read-only access decisions. Expiry never depends on a customer page visit."""
from django.db.models import Q
from django.utils import timezone
from apps.accounts.models import UserPlanAccessStatus, UserPlanTier
from .models import BillingSubscription, ManualPlanOrder


def eligible_manual_orders(user):
    now = timezone.now()
    return ManualPlanOrder.objects.filter(user=user, access_until__gt=now).filter(
        Q(status="paid") | Q(status="awaiting_payment", payment_due_at__gt=now)
    )


def effective_tier(user):
    manual = eligible_manual_orders(user).order_by("-created_at").first()
    if manual:
        return manual.tier
    subscription = BillingSubscription.objects.filter(user=user).first()
    if subscription:
        if subscription.status in {"active", "trialing"} and subscription.current_period_end and subscription.current_period_end > timezone.now():
            return subscription.tier
        return user.plan_tier
    if ManualPlanOrder.objects.filter(user=user).exists():
        return user.plan_tier
    return user.plan_tier


def reconcile_access(user):
    from .services import activate_paid_plan, expire_paid_access
    tier = effective_tier(user)
    if has_publication_access(user):
        if tier != user.plan_tier or user.plan_access_status != UserPlanAccessStatus.ACTIVE:
            activate_paid_plan(user, tier)
    elif user.plan_access_status != UserPlanAccessStatus.EXPIRED:
        expire_paid_access(user)
    return tier


def publication_users():
    """Paid publication, including the existing manual payment grace period."""
    from django.contrib.auth import get_user_model
    now = timezone.now()
    stripe_users = BillingSubscription.objects.filter(status__in=["active", "trialing"], current_period_end__gt=now).values("user_id")
    manual_users = ManualPlanOrder.objects.filter(access_until__gt=now).filter(Q(status="paid") | Q(status="awaiting_payment", payment_due_at__gt=now)).values("user_id")
    return get_user_model().objects.filter(is_active=True).filter(
        Q(pk__in=stripe_users) | Q(pk__in=manual_users) | Q(is_superuser=True)
    ).distinct()


def has_publication_access(user):
    return publication_users().filter(pk=user.pk).exists()
