import logging
import stripe
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from apps.accounts.models import User
from .models import BillingSubscription, StripeEvent
from .services import object_get, record_invoice_payment, sync_subscription_from_stripe

logger = logging.getLogger(__name__)


@transaction.atomic
def process_event(event):
    event_id = object_get(event, "id", "")
    event_type = object_get(event, "type", "")
    if not event_id.startswith("evt_"):
        raise ValueError("Missing Stripe event ID")
    row, _ = StripeEvent.objects.get_or_create(event_id=event_id, defaults={"event_type": event_type})
    row = StripeEvent.objects.select_for_update().get(pk=row.pk)
    if row.processed_at:
        return
    obj = object_get(object_get(event, "data", {}), "object", {})
    metadata = object_get(obj, "metadata", {}) or {}
    user_id = object_get(metadata, "user_id")
    subscription_id = object_get(obj, "subscription", "")
    if event_type.startswith("customer.subscription."):
        subscription_id = object_get(obj, "id", "")
    if not user_id and subscription_id:
        user_id = BillingSubscription.objects.filter(stripe_subscription_id=subscription_id).values_list("user_id", flat=True).first()
    if user_id:
        # Serialize retrieval and application, including differently ordered events.
        User.objects.select_for_update().filter(pk=user_id).first()
    stripe.api_key = settings.STRIPE_SECRET_KEY
    if event_type == "checkout.session.completed":
        if object_get(obj, "mode") == "subscription" and subscription_id:
            sync_subscription_from_stripe(stripe.Subscription.retrieve(subscription_id))
        elif object_get(metadata, "upgrade_type") == "plus_to_pro":
            # Old standalone payments require reconciliation/refund; never reinterpret
            # a Checkout Session as a Subscription or bill the customer a second time.
            from apps.notifications.services import notify_admin
            notify_admin(title="Legacy upgrade payment needs review", message="Review the standalone upgrade payment in Stripe before granting access or refunding it.", category="payment", severity="urgent", reference_key=f"stripe:{event_id}:legacy-upgrade")
    elif event_type.startswith("customer.subscription."):
        sync_subscription_from_stripe(stripe.Subscription.retrieve(subscription_id))
    elif event_type in {
        "invoice.paid",
        "invoice.payment_succeeded",
        "invoice.payment_failed",
        "invoice.payment_action_required",
    }:
        invoice = stripe.Invoice.retrieve(object_get(obj, "id"))
        record_invoice_payment(invoice)
    row.processed_at = timezone.now()
    row.save(update_fields=["processed_at"])
