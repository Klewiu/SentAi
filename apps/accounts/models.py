from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


class AccountType(models.TextChoices):
    CLIENT = "CLIENT", "Client"
    STAFF = "STAFF", "Staff"


class UserPlanTier(models.TextChoices):
    BASIC = "BASIC", "Basic"
    PLUS = "PLUS", "Plus"
    PRO = "PRO", "Pro"


USER_PLAN_ORGANIZATION_LIMITS = {
    UserPlanTier.BASIC: 1,
    UserPlanTier.PLUS: 2,
    UserPlanTier.PRO: 3,
}


class User(AbstractUser):
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
    paid_plan_started_at = models.DateTimeField(blank=True, null=True)
    country = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["username"]

    def __str__(self) -> str:
        return self.email or self.username

    def organization_limit(self) -> int:
        return USER_PLAN_ORGANIZATION_LIMITS.get(self.plan_tier, 1)

    def can_add_organization(self, current_count: int | None = None) -> bool:
        if self.is_superuser:
            return True
        organization_count = current_count
        if organization_count is None:
            organization_count = self.organizations.count()
        return organization_count < self.organization_limit()
