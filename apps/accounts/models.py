from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower


class AccountType(models.TextChoices):
    CLIENT = "CLIENT", "Client"
    STAFF = "STAFF", "Staff"


class UserPlanTier(models.TextChoices):
    BASIC = "BASIC", "Basic"
    PLUS = "PLUS", "Plus"
    PRO = "PRO", "Pro"


class UserPlanAccessStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    EXPIRED = "EXPIRED", "Expired"


USER_PLAN_ORGANIZATION_LIMITS = {
    UserPlanTier.BASIC: 1,
    UserPlanTier.PLUS: 2,
    UserPlanTier.PRO: 3,
}


class User(AbstractUser):
    registration_pending = models.BooleanField(default=False)
    pending_email = models.EmailField(blank=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    email = models.EmailField(unique=True)
    company_name = models.CharField(max_length=255, blank=True)
    preferred_language = models.CharField(
        max_length=2,
        choices=settings.LANGUAGES,
        default="en",
    )
    account_type = models.CharField(
        max_length=16,
        choices=AccountType.choices,
        default=AccountType.CLIENT,
    )
    plan_tier = models.CharField(
        max_length=16,
        choices=UserPlanTier.choices,
        default=UserPlanTier.BASIC,
    )
    plan_access_status = models.CharField(
        max_length=16,
        choices=UserPlanAccessStatus.choices,
        default=UserPlanAccessStatus.EXPIRED,
    )
    plan_selected_at = models.DateTimeField(blank=True, null=True)
    paid_plan_started_at = models.DateTimeField(blank=True, null=True)
    country = models.CharField(max_length=120, blank=True)
    closed_at = models.DateTimeField(blank=True, null=True)
    closed_display_name = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["username"]
        constraints = [models.UniqueConstraint(Lower("email"), name="unique_user_email_case_insensitive")]

    def __str__(self) -> str:
        return self.email or self.username

    def organization_limit(self) -> int:
        from apps.billing.access import effective_tier
        return USER_PLAN_ORGANIZATION_LIMITS.get(effective_tier(self), 1)

    def has_selected_plan(self) -> bool:
        return self.plan_selected_at is not None

    def can_add_organization(self, current_count: int | None = None) -> bool:
        if self.is_superuser:
            return True
        if not self.has_selected_plan():
            return False
        organization_count = current_count
        if organization_count is None:
            organization_count = self.organizations.count()
        return organization_count < self.organization_limit()


class AuthRateWindow(models.Model):
    key = models.CharField(max_length=64)
    window = models.BigIntegerField(db_index=True)
    count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["key", "window"], name="unique_auth_rate_window")]


class GoogleIdentity(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="google_identity")
    subject = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
