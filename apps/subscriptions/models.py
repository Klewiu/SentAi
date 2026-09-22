from django.db import models


class PlanTier(models.TextChoices):
    BASIC = "BASIC", "Basic"
    PLUS = "PLUS", "Plus"
    PRO = "PRO", "Pro"


class SubscriptionStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    CANCELED = "CANCELED", "Canceled"
    PAST_DUE = "PAST_DUE", "Past due"


PLAN_FEATURES = {
    PlanTier.BASIC: {
        "languages": 1,
        "advanced_formats": True,
        "company_md": True,
        "llms_txt": True,
        "social_profiles": 0,
        "tags": 0,
        "products": 0,
        "content_entries": 0,
    },
    PlanTier.PLUS: {
        "languages": 2,
        "advanced_formats": True,
        "company_md": True,
        "llms_txt": True,
        "social_profiles": 5,
        "tags": 25,
        "products": 0,
        "content_entries": 5,
    },
    PlanTier.PRO: {
        "languages": 3,
        "advanced_formats": True,
        "company_md": True,
        "llms_txt": True,
        "social_profiles": 6,
        "tags": 50,
        "products": 100,
        "content_entries": 15,
    },
}


class Subscription(models.Model):
    organization = models.OneToOneField(
        "companies.Organization",
        on_delete=models.CASCADE,
        related_name="subscription",
    )
    tier = models.CharField(max_length=16, choices=PlanTier.choices, default=PlanTier.BASIC)
    status = models.CharField(
        max_length=16,
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.ACTIVE,
    )
    started_at = models.DateTimeField(auto_now_add=True)
    renews_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["organization__name"]

    def __str__(self) -> str:
        return f"{self.organization.name} - {self.tier}"

    def feature_matrix(self) -> dict:
        return PLAN_FEATURES[self.tier]

    def limit_for(self, resource_name: str) -> int:
        value = self.feature_matrix()[resource_name]
        if isinstance(value, bool):
            return int(value)
        return value

    def supports(self, feature_name: str) -> bool:
        return bool(self.feature_matrix().get(feature_name, False))
