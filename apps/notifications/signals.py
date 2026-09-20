from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.accounts.models import AccountType, User
from apps.billing.models import BillingInvoice

from .services import notify_customer_invoice_available, notify_new_customer


@receiver(post_save, sender=User)
def create_customer_notification(sender, instance, created, **kwargs):
    if created and instance.account_type == AccountType.CLIENT and not instance.is_superuser:
        notify_new_customer(instance)


@receiver(post_save, sender=BillingInvoice)
def create_customer_invoice_notification(sender, instance, created, **kwargs):
    if created:
        notify_customer_invoice_available(instance)


@receiver(post_save, sender=User)
def update_customer_conditions(sender, instance, created, **kwargs):
    if not created and instance.account_type == AccountType.CLIENT:
        from .services import resolve_customer_condition
        resolve_customer_condition(instance, f"customer:{instance.pk}:plan-not-selected", instance.has_selected_plan())


from apps.billing.models import BillingProfile

@receiver(post_save, sender=BillingProfile)
def update_billing_condition(sender, instance, **kwargs):
    from .services import resolve_customer_condition
    resolve_customer_condition(instance.user, f"customer:{instance.user_id}:billing-incomplete", instance.is_complete())
