from django.db.models.signals import pre_save
from django.dispatch import receiver
from rest_framework.authtoken.models import Token
from .models import User


@receiver(pre_save, sender=User)
def revoke_tokens_on_credential_change(sender, instance, **kwargs):
    if not instance.pk:
        return
    old = User.objects.filter(pk=instance.pk).values("password", "email", "is_active").first()
    if old and any(old[field] != getattr(instance, field) for field in old):
        Token.objects.filter(user_id=instance.pk).delete()
