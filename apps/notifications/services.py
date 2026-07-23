from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import UserPlanTier
from apps.billing.models import BillingInvoice, BillingPayment, BillingPaymentStatus, BillingSubscription, ManualPlanOrder, ManualPlanOrderStatus

from .models import AdminNotification, CustomerNotification, NotificationCategory, NotificationSeverity


def notify_admin(*, title, message, category, severity=NotificationSeverity.INFO, customer=None, action_url="", reference_key=None):
    defaults = {
        "title": title,
        "message": message,
        "category": category,
        "severity": severity,
        "customer": customer,
        "action_url": action_url,
    }
    if reference_key:
        notification, created = AdminNotification.objects.get_or_create(reference_key=reference_key, defaults=defaults)
        if not created and notification.closed_at is None:
            changed = []
            for field, value in defaults.items():
                if getattr(notification, field) != value:
                    setattr(notification, field, value)
                    changed.append(field)
            if changed:
                notification.save(update_fields=[*changed, "updated_at"])
        return notification
    return AdminNotification.objects.create(**defaults)


def notify_customer(*, user, title, message, category, severity=NotificationSeverity.INFO, action_url="", reference_key=None, title_pl="", message_pl=""):
    defaults = {
        "user": user,
        "title": title,
        "message": message,
        "title_pl": title_pl,
        "message_pl": message_pl,
        "category": category,
        "severity": severity,
        "action_url": action_url,
    }
    if reference_key:
        notification, created = CustomerNotification.objects.get_or_create(reference_key=reference_key, defaults=defaults)
        if not created and notification.closed_at is None:
            changed = []
            for field, value in defaults.items():
                if getattr(notification, field) != value:
                    setattr(notification, field, value)
                    changed.append(field)
            if changed:
                notification.save(update_fields=[*changed, "updated_at"])
        return notification
    return CustomerNotification.objects.create(**defaults)


def close_notification(reference_key, closed_by=None):
    if not reference_key:
        return 0
    return AdminNotification.objects.filter(reference_key=reference_key, closed_at__isnull=True).update(
        closed_at=timezone.now(),
        closed_by=closed_by,
        updated_at=timezone.now(),
    )


def notify_new_customer(user):
    notify_admin(
        title="New customer account",
        message=f"{user.email} created a customer account.",
        category=NotificationCategory.CUSTOMER,
        severity=NotificationSeverity.INFO,
        customer=user,
        action_url=reverse("dashboard:client-detail", args=[user.pk]),
        reference_key=f"user:{user.pk}:created",
    )
    notify_customer_welcome(user)


def notify_customer_welcome(user):
    notify_customer(
        user=user,
        title="Welcome to xoaila",
        message="To start using the platform: complete your company form, fill in billing details, and choose a plan. This unlocks your company page and billing flow.",
        title_pl="Witamy w xoaila",
        message_pl="Aby zacząć korzystać z platformy: uzupełnij formularz firmy, dodaj dane do faktury i wybierz plan. To odblokuje stronę firmy i obsługę płatności.",
        category=NotificationCategory.CUSTOMER,
        severity=NotificationSeverity.INFO,
        action_url=reverse("dashboard:plan-update"),
        reference_key=f"customer:{user.pk}:welcome",
    )


def notify_customer_billing_incomplete(user):
    notify_customer(
        user=user,
        title="Complete billing details",
        message="Add your billing details so invoices and paid plans can work correctly.",
        title_pl="Uzupełnij dane do faktury",
        message_pl="Dodaj dane do faktury, aby faktury i płatne plany działały poprawnie.",
        category=NotificationCategory.CUSTOMER,
        severity=NotificationSeverity.WARNING,
        action_url=reverse("dashboard:billing-profile"),
        reference_key=f"customer:{user.pk}:billing-incomplete",
    )


def notify_customer_plan_not_selected(user):
    notify_customer(
        user=user,
        title="Choose a plan",
        message="Select Basic, Plus, Pro or Pro Manual to activate the correct limits and workflow for your account.",
        title_pl="Wybierz plan",
        message_pl="Wybierz Basic, Plus, Pro albo Pro Manual, aby aktywować właściwe limity i przepływ pracy dla konta.",
        category=NotificationCategory.PLAN,
        severity=NotificationSeverity.WARNING,
        action_url=reverse("dashboard:plan-update"),
        reference_key=f"customer:{user.pk}:plan-not-selected",
    )


def notify_plan_selected(user, plan_tier):
    notify_admin(
        title=f"New {plan_tier} plan selected",
        message=f"{user.email} selected the {plan_tier} plan.",
        category=NotificationCategory.PLAN,
        severity=NotificationSeverity.INFO,
        customer=user,
        action_url=reverse("dashboard:client-detail", args=[user.pk]),
        reference_key=f"user:{user.pk}:plan:{plan_tier}:{user.plan_selected_at or user.date_joined}",
    )


def notify_manual_order_created(order):
    notify_admin(
        title="New Pro Manual order",
        message=f"{order.user.email} activated Pro Manual. Payment is due by {order.payment_due_at:%Y-%m-%d %H:%M}.",
        category=NotificationCategory.MANUAL_PLAN,
        severity=NotificationSeverity.WARNING,
        customer=order.user,
        action_url=reverse("dashboard:billing-overview"),
        reference_key=f"manual-order:{order.pk}:created",
    )


def notify_invoice_needed_for_payment(payment):
    notify_admin(
        title="Stripe payment needs invoice",
        message=f"{payment.user.email} paid {payment.formatted_amount()}. Upload and send an invoice.",
        category=NotificationCategory.INVOICE,
        severity=NotificationSeverity.WARNING,
        customer=payment.user,
        action_url=reverse("dashboard:billing-invoices-admin"),
        reference_key=f"payment:{payment.pk}:invoice-needed",
    )


def notify_invoice_needed_for_manual_order(order):
    notify_admin(
        title="Pro Manual payment needs invoice",
        message=f"{order.user.email} paid {order.formatted_amount()}. Upload and send an invoice.",
        category=NotificationCategory.INVOICE,
        severity=NotificationSeverity.WARNING,
        customer=order.user,
        action_url=reverse("dashboard:billing-invoices-admin"),
        reference_key=f"manual-order:{order.pk}:invoice-needed",
    )


def notify_manual_order_overdue(order):
    notify_admin(
        title="Pro Manual payment overdue",
        message=f"{order.user.email} has not paid Pro Manual by {order.payment_due_at:%Y-%m-%d %H:%M}. Review and disable the plan if needed.",
        category=NotificationCategory.MANUAL_PLAN,
        severity=NotificationSeverity.URGENT,
        customer=order.user,
        action_url=reverse("dashboard:billing-overview"),
        reference_key=f"manual-order:{order.pk}:overdue",
    )


def notify_subscription_past_due(subscription):
    notify_admin(
        title="Stripe subscription payment issue",
        message=f"{subscription.user.email} has subscription status {subscription.status}. Payment method may need attention.",
        category=NotificationCategory.STRIPE,
        severity=NotificationSeverity.URGENT,
        customer=subscription.user,
        action_url=reverse("dashboard:billing-overview"),
        reference_key=f"subscription:{subscription.pk}:status:{subscription.status}",
    )


def notify_subscription_canceling(subscription):
    notify_admin(
        title="Stripe subscription renewal canceled",
        message=f"{subscription.user.email} canceled renewal. Access remains until {subscription.current_period_end:%Y-%m-%d}." if subscription.current_period_end else f"{subscription.user.email} canceled renewal.",
        category=NotificationCategory.STRIPE,
        severity=NotificationSeverity.INFO,
        customer=subscription.user,
        action_url=reverse("dashboard:billing-overview"),
        reference_key=f"subscription:{subscription.pk}:canceling",
    )


def close_invoice_needed_for_payment(payment, closed_by=None):
    close_notification(f"payment:{payment.pk}:invoice-needed", closed_by=closed_by)


def close_invoice_needed_for_manual_order(order, closed_by=None):
    close_notification(f"manual-order:{order.pk}:invoice-needed", closed_by=closed_by)


def close_manual_order_overdue(order, closed_by=None):
    close_notification(f"manual-order:{order.pk}:overdue", closed_by=closed_by)


def notify_customer_invoice_available(invoice):
    notify_customer(
        user=invoice.user,
        title="New invoice available",
        message=f"Invoice {invoice.invoice_number} is available in your account.",
        title_pl="Nowa faktura dostępna",
        message_pl=f"Faktura {invoice.invoice_number} jest dostępna na Twoim koncie.",
        category=NotificationCategory.INVOICE,
        severity=NotificationSeverity.SUCCESS,
        action_url=reverse("dashboard:customer-invoices"),
        reference_key=f"customer:{invoice.user_id}:invoice:{invoice.pk}",
    )


def notify_customer_subscription_renewal(subscription):
    if not subscription.current_period_end:
        return
    notify_customer(
        user=subscription.user,
        title="Subscription renews soon",
        message=f"Your {subscription.get_tier_display()} subscription renews on {subscription.current_period_end:%Y-%m-%d}. Make sure your card/payment method is valid.",
        title_pl="Subskrypcja wkrótce się odnowi",
        message_pl=f"Twoja subskrypcja {subscription.get_tier_display()} odnowi się {subscription.current_period_end:%Y-%m-%d}. Upewnij się, że karta lub metoda płatności jest aktualna.",
        category=NotificationCategory.STRIPE,
        severity=NotificationSeverity.WARNING,
        action_url=reverse("dashboard:billing-portal"),
        reference_key=f"customer:{subscription.user_id}:stripe-renewal:{subscription.pk}:{subscription.current_period_end.date()}",
    )


def notify_customer_subscription_payment_issue(subscription):
    notify_customer(
        user=subscription.user,
        title="Subscription payment needs attention",
        message="Your subscription payment has an issue. Please update your payment method to keep access active.",
        title_pl="Płatność subskrypcji wymaga uwagi",
        message_pl="Wystąpił problem z płatnością subskrypcji. Zaktualizuj metodę płatności, aby utrzymać dostęp.",
        category=NotificationCategory.PAYMENT,
        severity=NotificationSeverity.URGENT,
        action_url=reverse("dashboard:billing-portal"),
        reference_key=f"customer:{subscription.user_id}:stripe-payment-issue:{subscription.pk}:{subscription.status}",
    )


def notify_customer_manual_payment_due(order):
    notify_customer(
        user=order.user,
        title="Pro Manual payment due",
        message=f"Your Pro Manual bank transfer is due by {order.payment_due_at:%Y-%m-%d}. Use reference: {order.payment_reference}.",
        title_pl="Termin płatności Pro Manual",
        message_pl=f"Przelew za Pro Manual należy opłacić do {order.payment_due_at:%Y-%m-%d}. Użyj tytułu przelewu: {order.payment_reference}.",
        category=NotificationCategory.MANUAL_PLAN,
        severity=NotificationSeverity.WARNING,
        action_url=reverse("dashboard:plan-update"),
        reference_key=f"customer:{order.user_id}:manual-payment-due:{order.pk}",
    )


def notify_customer_manual_renewal(order, days):
    notify_customer(
        user=order.user,
        title=f"Pro Manual ends in {days} days",
        message=f"Your Pro Manual access ends on {order.access_until:%Y-%m-%d}. Contact us or renew Pro Manual to continue annual access.",
        title_pl=f"Pro Manual kończy się za {days} dni",
        message_pl=f"Twój dostęp Pro Manual kończy się {order.access_until:%Y-%m-%d}. Odnów Pro Manual, aby kontynuować roczny dostęp.",
        category=NotificationCategory.MANUAL_PLAN,
        severity=NotificationSeverity.WARNING if days == 30 else NotificationSeverity.URGENT,
        action_url=reverse("dashboard:plan-update"),
        reference_key=f"customer:{order.user_id}:manual-renewal-{days}:{order.pk}:{order.access_until.date()}",
    )


def scan_admin_notifications():
    for payment in BillingPayment.objects.filter(status=BillingPaymentStatus.PAID).select_related("user").prefetch_related("invoices"):
        if not payment.invoices.exists():
            notify_invoice_needed_for_payment(payment)
    for order in ManualPlanOrder.objects.filter(status=ManualPlanOrderStatus.PAID).select_related("user").prefetch_related("invoices"):
        if not order.invoices.exists():
            notify_invoice_needed_for_manual_order(order)
    for order in ManualPlanOrder.objects.filter(status=ManualPlanOrderStatus.AWAITING_PAYMENT, payment_due_at__lt=timezone.now()).select_related("user"):
        notify_manual_order_overdue(order)
    for subscription in BillingSubscription.objects.filter(status__in=["past_due", "unpaid"]).select_related("user"):
        notify_subscription_past_due(subscription)
    for subscription in BillingSubscription.objects.filter(cancel_at_period_end=True).select_related("user"):
        notify_subscription_canceling(subscription)

    paid_manual_with_invoice = BillingInvoice.objects.filter(manual_order__isnull=False).select_related("manual_order")
    for invoice in paid_manual_with_invoice:
        close_invoice_needed_for_manual_order(invoice.manual_order)
    paid_stripe_with_invoice = BillingInvoice.objects.filter(payment__isnull=False).select_related("payment")
    for invoice in paid_stripe_with_invoice:
        close_invoice_needed_for_payment(invoice.payment)


def scan_customer_notifications(user):
    notify_customer_welcome(user)
    if not user.has_selected_plan():
        notify_customer_plan_not_selected(user)
    billing_profile = getattr(user, "billing_profile", None)
    if not billing_profile or not billing_profile.is_complete():
        notify_customer_billing_incomplete(user)

    now = timezone.now()
    renewal_cutoff = now + timedelta(days=14)
    subscription = getattr(user, "billing_subscription", None)
    if subscription:
        if subscription.status in {"active", "trialing"} and subscription.current_period_end and now <= subscription.current_period_end <= renewal_cutoff:
            notify_customer_subscription_renewal(subscription)
        if subscription.status in {"past_due", "unpaid"}:
            notify_customer_subscription_payment_issue(subscription)

    for invoice in BillingInvoice.objects.filter(user=user):
        notify_customer_invoice_available(invoice)

    for order in ManualPlanOrder.objects.filter(user=user, status=ManualPlanOrderStatus.AWAITING_PAYMENT):
        if now <= order.payment_due_at <= now + timedelta(days=7) or order.payment_due_at < now:
            notify_customer_manual_payment_due(order)

    for order in ManualPlanOrder.objects.filter(user=user, status=ManualPlanOrderStatus.PAID):
        days_left = (order.access_until.date() - now.date()).days
        if 0 <= days_left <= 30:
            notify_customer_manual_renewal(order, 30)
        if 0 <= days_left <= 14:
            notify_customer_manual_renewal(order, 14)
