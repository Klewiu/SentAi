from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from django.db.models import Q
from django.core.exceptions import ObjectDoesNotExist

from apps.accounts.models import UserPlanTier
from apps.billing.models import BillingInvoice, BillingPayment, BillingPaymentStatus, BillingSubscription, ManualPlanOrder, ManualPlanOrderStatus

from .models import AdminNotification, CustomerNotification, NotificationCategory, NotificationSeverity


def notify_admin(*, title, message, category, severity=NotificationSeverity.INFO, customer=None, action_url="", reference_key=None, title_pl="", message_pl=""):
    defaults = {
        "title": title,
        "message": message,
        "title_pl": title_pl,
        "message_pl": message_pl,
        "category": category,
        "severity": severity,
        "customer": customer,
        "action_url": action_url,
    }
    if reference_key:
        notification, created = AdminNotification.objects.get_or_create(reference_key=reference_key, defaults=defaults)
        if not created and notification.resolved_at is not None:
            notification.resolved_at = None
            notification.closed_at = None
            notification.save(update_fields=["resolved_at", "closed_at", "updated_at"])
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
        if not created and notification.resolved_at is not None:
            notification.resolved_at = None
            notification.closed_at = None
            notification.save(update_fields=["resolved_at", "closed_at", "updated_at"])
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
    return AdminNotification.objects.filter(reference_key=reference_key, resolved_at__isnull=True).update(
        closed_at=timezone.now(),
        resolved_at=timezone.now(),
        closed_by=closed_by,
        updated_at=timezone.now(),
    )


def notify_new_customer(user):
    notify_admin(
        title="New customer account",
        message=f"{user.email} created a customer account.",
        title_pl="Nowe konto klienta",
        message_pl=f"{user.email} utworzył konto klienta.",
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
        message="Select Basic, Plus, or Pro, then choose a Stripe subscription or annual bank transfer.",
        title_pl="Wybierz plan",
        message_pl="Wybierz Basic, Plus lub Pro, a następnie subskrypcję Stripe albo roczną płatność przelewem.",
        category=NotificationCategory.PLAN,
        severity=NotificationSeverity.WARNING,
        action_url=reverse("dashboard:plan-update"),
        reference_key=f"customer:{user.pk}:plan-not-selected",
    )


def notify_plan_selected(user, plan_tier):
    notify_admin(
        title=f"New {plan_tier} plan selected",
        message=f"{user.email} selected the {plan_tier} plan.",
        title_pl=f"Wybrano nowy plan {plan_tier}",
        message_pl=f"{user.email} wybrał plan {plan_tier}.",
        category=NotificationCategory.PLAN,
        severity=NotificationSeverity.INFO,
        customer=user,
        action_url=reverse("dashboard:client-detail", args=[user.pk]),
        reference_key=f"user:{user.pk}:plan:{plan_tier}:{user.plan_selected_at or user.date_joined}",
    )


def notify_manual_order_created(order):
    is_renewal = ManualPlanOrder.objects.filter(
        user=order.user,
        paid_at__isnull=False,
        created_at__lt=order.created_at,
    ).exclude(pk=order.pk).exists()
    notify_admin(
        title=(f"{order.get_tier_display()} Manual renewal order" if is_renewal else f"New {order.get_tier_display()} Manual order"),
        message=f"{order.user.email} activated {'another annual period of' if is_renewal else ''} {order.get_tier_display()} Manual. Payment is due by {order.payment_due_at:%Y-%m-%d %H:%M}.",
        title_pl=(f"Odnowienie {order.get_tier_display()} Manual" if is_renewal else f"Nowe zamówienie {order.get_tier_display()} Manual"),
        message_pl=f"{order.user.email} aktywował {'kolejny roczny okres planu' if is_renewal else 'plan'} {order.get_tier_display()} Manual. Termin płatności: {order.payment_due_at:%Y-%m-%d %H:%M}.",
        category=NotificationCategory.MANUAL_PLAN,
        severity=NotificationSeverity.WARNING,
        customer=order.user,
        action_url=reverse("dashboard:billing-overview"),
        reference_key=f"manual-order:{order.pk}:created",
    )


def notify_invoice_needed_for_payment(payment):
    is_renewal = payment.billing_reason == "subscription_cycle" or BillingPayment.objects.filter(
        user=payment.user,
        status=BillingPaymentStatus.PAID,
        created_at__lt=payment.created_at,
    ).exclude(pk=payment.pk).exists()
    notify_admin(
        title="Stripe renewal payment needs invoice" if is_renewal else "Stripe payment needs invoice",
        message=f"{payment.user.email} paid {payment.formatted_amount()} for {'a subscription renewal' if is_renewal else 'a new subscription'}. Upload and send an invoice.",
        title_pl="Odnowienie Stripe wymaga wystawienia faktury" if is_renewal else "Płatność Stripe wymaga wystawienia faktury",
        message_pl=f"{payment.user.email} zapłacił {payment.formatted_amount()} za {'odnowienie subskrypcji' if is_renewal else 'nową subskrypcję'}. Wystaw i wyślij fakturę.",
        category=NotificationCategory.INVOICE,
        severity=NotificationSeverity.WARNING,
        customer=payment.user,
        action_url=reverse("dashboard:billing-invoices-admin"),
        reference_key=f"payment:{payment.pk}:invoice-needed",
    )


def notify_invoice_needed_for_manual_order(order):
    is_renewal = ManualPlanOrder.objects.filter(
        user=order.user,
        paid_at__isnull=False,
        created_at__lt=order.created_at,
    ).exclude(pk=order.pk).exists()
    notify_admin(
        title=f"{order.get_tier_display()} Manual {'renewal ' if is_renewal else ''}payment needs invoice",
        message=f"{order.user.email} paid {order.formatted_amount()} for {'a Manual renewal' if is_renewal else 'a new Manual plan'}. Upload and send an invoice.",
        title_pl=(f"Odnowienie {order.get_tier_display()} Manual wymaga wystawienia faktury" if is_renewal else f"Płatność {order.get_tier_display()} Manual wymaga wystawienia faktury"),
        message_pl=f"{order.user.email} zapłacił {order.formatted_amount()} za {'odnowienie planu Manual' if is_renewal else 'nowy plan Manual'}. Wystaw i wyślij fakturę.",
        category=NotificationCategory.INVOICE,
        severity=NotificationSeverity.WARNING,
        customer=order.user,
        action_url=reverse("dashboard:billing-invoices-admin"),
        reference_key=f"manual-order:{order.pk}:invoice-needed",
    )


def notify_manual_order_overdue(order):
    notify_admin(
        title=f"{order.get_tier_display()} Manual payment overdue",
        message=f"{order.user.email} has not paid {order.get_tier_display()} Manual by {order.payment_due_at:%Y-%m-%d %H:%M}. Review and disable the plan if needed.",
        title_pl=f"Przekroczony termin płatności {order.get_tier_display()} Manual",
        message_pl=f"{order.user.email} nie opłacił planu {order.get_tier_display()} Manual do {order.payment_due_at:%Y-%m-%d %H:%M}. Sprawdź zamówienie i w razie potrzeby wyłącz plan.",
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
        title_pl="Problem z płatnością subskrypcji Stripe",
        message_pl=f"Subskrypcja klienta {subscription.user.email} ma status {subscription.status}. Metoda płatności może wymagać aktualizacji.",
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
        title_pl="Anulowano odnowienie subskrypcji Stripe",
        message_pl=f"{subscription.user.email} anulował odnowienie. Dostęp pozostaje aktywny do {subscription.current_period_end:%Y-%m-%d}." if subscription.current_period_end else f"{subscription.user.email} anulował odnowienie.",
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
    if not invoice.user.is_active:
        return None
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


def backfill_admin_notification_translations():
    """Fill Polish text for notifications created before bilingual admin alerts."""
    notifications = AdminNotification.objects.filter(Q(title_pl="") | Q(message_pl="")).select_related("customer")
    for notification in notifications.iterator(chunk_size=100):
        reference = notification.reference_key or ""
        title_pl = ""
        message_pl = ""
        try:
            if reference.startswith("user:") and reference.endswith(":created") and notification.customer:
                title_pl = "Nowe konto klienta"
                message_pl = f"{notification.customer.email} utworzył konto klienta."
            elif ":plan:" in reference and notification.customer:
                tier = reference.split(":plan:", 1)[1].split(":", 1)[0]
                title_pl = f"Wybrano nowy plan {tier}"
                message_pl = f"{notification.customer.email} wybrał plan {tier}."
            elif reference.startswith("manual-order:"):
                order_id = int(reference.split(":", 2)[1])
                order = ManualPlanOrder.objects.select_related("user").get(pk=order_id)
                if reference.endswith(":created"):
                    title_pl = f"Nowe zamówienie {order.get_tier_display()} Manual"
                    message_pl = f"{order.user.email} aktywował plan {order.get_tier_display()} Manual. Termin płatności: {order.payment_due_at:%Y-%m-%d %H:%M}."
                elif reference.endswith(":invoice-needed"):
                    title_pl = f"Płatność {order.get_tier_display()} Manual wymaga wystawienia faktury"
                    message_pl = f"{order.user.email} zapłacił {order.formatted_amount()}. Wystaw i wyślij fakturę."
                elif reference.endswith(":overdue"):
                    title_pl = f"Przekroczony termin płatności {order.get_tier_display()} Manual"
                    message_pl = f"{order.user.email} nie opłacił planu {order.get_tier_display()} Manual do {order.payment_due_at:%Y-%m-%d %H:%M}. Sprawdź zamówienie i w razie potrzeby wyłącz plan."
            elif reference.startswith("payment:") and reference.endswith(":invoice-needed"):
                payment_id = int(reference.split(":", 2)[1])
                payment = BillingPayment.objects.select_related("user").get(pk=payment_id)
                title_pl = "Płatność Stripe wymaga wystawienia faktury"
                message_pl = f"{payment.user.email} zapłacił {payment.formatted_amount()}. Wystaw i wyślij fakturę."
            elif reference.startswith("organization:") and reference.endswith(":verification-needed"):
                from apps.companies.models import Organization
                organization_id = int(reference.split(":", 2)[1])
                organization = Organization.objects.get(pk=organization_id)
                title_pl = "Profil firmy wymaga weryfikacji"
                message_pl = f"Profil firmy {organization.name} został przesłany i oczekuje na weryfikację administratora."
            elif reference.startswith("subscription:"):
                subscription_id = int(reference.split(":", 2)[1])
                subscription = BillingSubscription.objects.select_related("user").get(pk=subscription_id)
                if reference.endswith(":canceling"):
                    title_pl = "Anulowano odnowienie subskrypcji Stripe"
                    message_pl = f"{subscription.user.email} anulował odnowienie. Dostęp pozostaje aktywny do {subscription.current_period_end:%Y-%m-%d}." if subscription.current_period_end else f"{subscription.user.email} anulował odnowienie."
                elif ":status:" in reference:
                    title_pl = "Problem z płatnością subskrypcji Stripe"
                    message_pl = f"Subskrypcja klienta {subscription.user.email} ma status {subscription.status}. Metoda płatności może wymagać aktualizacji."
        except (ValueError, ObjectDoesNotExist):
            continue
        if title_pl and message_pl:
            AdminNotification.objects.filter(pk=notification.pk).update(title_pl=title_pl, message_pl=message_pl)


def notify_organization_verification_needed(organization):
    notify_admin(
        title="Company profile needs verification",
        message=f"{organization.name} was submitted and is waiting for administrator verification.",
        title_pl="Profil firmy wymaga weryfikacji",
        message_pl=f"Profil firmy {organization.name} został przesłany i oczekuje na weryfikację administratora.",
        category=NotificationCategory.CUSTOMER,
        severity=NotificationSeverity.WARNING,
        customer=organization.owner,
        action_url=reverse("dashboard:client-detail", args=[organization.owner_id]),
        reference_key=f"organization:{organization.pk}:verification-needed",
    )


def close_organization_verification_needed(organization):
    close_notification(f"organization:{organization.pk}:verification-needed")


def notify_customer_subscription_renewal(subscription):
    if not subscription.current_period_end:
        return
    notify_customer(
        user=subscription.user,
        title="Subscription renews soon",
        message=f"Your {subscription.get_tier_display()} billing period ends on {subscription.current_period_end:%Y-%m-%d}. Stripe will soon charge the next annual subscription fee. Make sure your payment method is valid.",
        title_pl="Subskrypcja wkrótce się odnowi",
        message_pl=f"Okres rozliczeniowy subskrypcji {subscription.get_tier_display()} kończy się {subscription.current_period_end:%Y-%m-%d}. Stripe wkrótce pobierze opłatę za kolejny rok. Upewnij się, że metoda płatności jest aktualna.",
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
        title=f"{order.get_tier_display()} Manual payment due",
        message=f"Your {order.get_tier_display()} Manual bank transfer is due by {order.payment_due_at:%Y-%m-%d}. Use reference: {order.payment_reference}.",
        title_pl=f"Termin płatności {order.get_tier_display()} Manual",
        message_pl=f"Przelew za {order.get_tier_display()} Manual należy opłacić do {order.payment_due_at:%Y-%m-%d}. Użyj tytułu przelewu: {order.payment_reference}.",
        category=NotificationCategory.MANUAL_PLAN,
        severity=NotificationSeverity.WARNING,
        action_url=reverse("dashboard:plan-update"),
        reference_key=f"customer:{order.user_id}:manual-payment-due:{order.pk}",
    )


def notify_customer_manual_renewal(order, days):
    notify_customer(
        user=order.user,
        title=f"{order.get_tier_display()} Manual ends in {days} days",
        message=f"Your {order.get_tier_display()} Manual plan ends on {order.access_until:%Y-%m-%d}. Purchase the plan again to keep company profiles published without interruption.",
        title_pl=f"{order.get_tier_display()} Manual kończy się za {days} dni",
        message_pl=f"Twój plan {order.get_tier_display()} Manual kończy się {order.access_until:%Y-%m-%d}. Wykup plan ponownie, aby zachować ciągłość publikacji profili firm.",
        category=NotificationCategory.MANUAL_PLAN,
        severity=NotificationSeverity.URGENT,
        action_url=reverse("dashboard:plan-update"),
        reference_key=f"customer:{order.user_id}:manual-renewal-{days}:{order.pk}:{order.access_until.date()}",
    )


def notify_admin_subscription_renewal(subscription):
    if not subscription.current_period_end:
        return
    notify_admin(
        title="Stripe subscription renews within 14 days",
        message=f"{subscription.user.email}'s {subscription.get_tier_display()} billing period ends on {subscription.current_period_end:%Y-%m-%d}. Stripe will charge the next annual fee.",
        title_pl="Subskrypcja Stripe odnowi się w ciągu 14 dni",
        message_pl=f"Okres rozliczeniowy planu {subscription.get_tier_display()} klienta {subscription.user.email} kończy się {subscription.current_period_end:%Y-%m-%d}. Stripe pobierze opłatę za kolejny rok.",
        category=NotificationCategory.STRIPE,
        severity=NotificationSeverity.WARNING,
        customer=subscription.user,
        action_url=reverse("dashboard:client-detail", args=[subscription.user_id]),
        reference_key=f"subscription:{subscription.pk}:renewal-14:{subscription.current_period_end.date()}",
    )


def notify_admin_manual_renewal(order):
    notify_admin(
        title=f"{order.get_tier_display()} Manual ends within 14 days",
        message=f"{order.user.email}'s {order.get_tier_display()} Manual plan ends on {order.access_until:%Y-%m-%d}. The customer must purchase a new plan to maintain publication continuity.",
        title_pl=f"{order.get_tier_display()} Manual kończy się w ciągu 14 dni",
        message_pl=f"Plan {order.get_tier_display()} Manual klienta {order.user.email} kończy się {order.access_until:%Y-%m-%d}. Klient musi ponownie wykupić plan, aby zachować ciągłość publikacji.",
        category=NotificationCategory.MANUAL_PLAN,
        severity=NotificationSeverity.WARNING,
        customer=order.user,
        action_url=reverse("dashboard:client-detail", args=[order.user_id]),
        reference_key=f"manual-order:{order.pk}:renewal-14:{order.access_until.date()}",
    )


def scan_admin_notifications():
    reconcile_notification_conditions()
    backfill_admin_notification_translations()
    from apps.companies.models import Organization, VerificationStatus

    for organization in Organization.objects.filter(
        owner__is_active=True,
        verification_status=VerificationStatus.UNVERIFIED,
    ).select_related("owner").iterator(chunk_size=100):
        notify_organization_verification_needed(organization)
    for organization in Organization.objects.filter(
        verification_status=VerificationStatus.HUMAN_ADMIN_VERIFIED,
    ).iterator(chunk_size=100):
        close_organization_verification_needed(organization)
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

    now = timezone.now()
    renewal_cutoff = now + timedelta(days=14)
    for subscription in BillingSubscription.objects.filter(
        user__is_active=True,
        status__in=["active", "trialing"],
        cancel_at_period_end=False,
        current_period_end__gte=now,
        current_period_end__lte=renewal_cutoff,
    ).select_related("user"):
        notify_admin_subscription_renewal(subscription)
    for order in ManualPlanOrder.objects.filter(
        user__is_active=True,
        status=ManualPlanOrderStatus.PAID,
        access_until__gte=now,
        access_until__lte=renewal_cutoff,
    ).select_related("user"):
        notify_admin_manual_renewal(order)

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

    resolve_customer_condition(user, f"customer:{user.pk}:plan-not-selected", user.has_selected_plan())
    resolve_customer_condition(user, f"customer:{user.pk}:billing-incomplete", bool(billing_profile and billing_profile.is_complete()))
    now = timezone.now()
    renewal_cutoff = now + timedelta(days=14)
    subscription = getattr(user, "billing_subscription", None)
    if subscription:
        if not subscription.cancel_at_period_end and subscription.status in {"active", "trialing"} and subscription.current_period_end and now <= subscription.current_period_end <= renewal_cutoff:
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
        if 0 <= days_left <= 14:
            notify_customer_manual_renewal(order, 14)


def resolve_customer_condition(user, reference, resolved):
    if resolved:
        CustomerNotification.objects.filter(user=user, reference_key=reference, resolved_at__isnull=True).update(resolved_at=timezone.now(), closed_at=timezone.now())


def reconcile_notification_conditions():
    """Close obsolete conditions, retaining dismissed-vs-resolved distinction."""
    now = timezone.now()
    for subscription in BillingSubscription.objects.select_related("user"):
        for status in ("past_due", "unpaid"):
            if subscription.status != status:
                close_notification(f"subscription:{subscription.pk}:status:{status}")
                resolve_customer_condition(subscription.user, f"customer:{subscription.user_id}:stripe-payment-issue:{subscription.pk}:{status}", True)
        if not subscription.cancel_at_period_end or subscription.status not in {"active", "trialing", "past_due"}:
            close_notification(f"subscription:{subscription.pk}:canceling")
        if subscription.cancel_at_period_end or subscription.status not in {"active", "trialing"}:
            CustomerNotification.objects.filter(user=subscription.user, reference_key__startswith=f"customer:{subscription.user_id}:stripe-renewal:{subscription.pk}:", resolved_at__isnull=True).update(resolved_at=now, closed_at=now)
    for order in ManualPlanOrder.objects.exclude(status="awaiting_payment"):
        close_manual_order_overdue(order)
        resolve_customer_condition(order.user, f"customer:{order.user_id}:manual-payment-due:{order.pk}", True)
