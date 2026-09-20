from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.utils import timezone
from .models import Organization, Product, Tag, SocialProfile, ContentEntry, Page


@receiver(post_save, sender=Organization)
def organization_verification_changed(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return
    from apps.notifications.services import (
        close_organization_verification_needed,
        notify_organization_verification_needed,
    )

    if instance.verification_status == "human_admin_verified":
        close_organization_verification_needed(instance)
    else:
        notify_organization_verification_needed(instance)


@receiver(post_delete, sender=Organization)
def removed_organization_closes_verification_notice(sender, instance, **kwargs):
    from apps.notifications.services import close_organization_verification_needed

    close_organization_verification_needed(instance)


def touch_organization(organization_id):
    Organization.objects.filter(pk=organization_id).update(
        updated_at=timezone.now(),
    )


@receiver(post_save, sender=Product)
@receiver(post_delete, sender=Product)
@receiver(post_save, sender=Tag)
@receiver(post_delete, sender=Tag)
@receiver(post_save, sender=SocialProfile)
@receiver(post_delete, sender=SocialProfile)
@receiver(post_save, sender=ContentEntry)
@receiver(post_delete, sender=ContentEntry)
@receiver(post_save, sender=Page)
@receiver(post_delete, sender=Page)
def related_content_changed(sender, instance, **kwargs):
    if not kwargs.get("raw"):
        # Related content participates in the profile revision and ETag, but an
        # owner edit does not cancel an existing administrator verification.
        touch_organization(instance.organization_id)
