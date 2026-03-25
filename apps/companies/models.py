from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify
import json


class OrganizationType(models.TextChoices):
    MANUFACTURING = "manufacturing", "Manufacturing"
    SERVICES = "services", "Services"
    TRADING = "trading", "Trading"
    OTHER = "other", "Other"


class Organization(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="organizations",
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    company_type = models.CharField(max_length=32, choices=OrganizationType.choices, default=OrganizationType.OTHER)
    website_url = models.URLField(blank=True)
    phone_number = models.CharField(max_length=64, blank=True)
    address_line = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=120, blank=True)
    postal_code = models.CharField(max_length=24, blank=True)
    country = models.CharField(max_length=120, blank=True)
    primary_language = models.CharField(max_length=2, choices=settings.LANGUAGES, default="pl")
    content_languages = models.JSONField(default=list, help_text="Selected content languages for this organization")
    short_description_en = models.CharField(max_length=280, blank=True)
    short_description_pl = models.CharField(max_length=280, blank=True)
    long_description_en = models.TextField(blank=True)
    long_description_pl = models.TextField(blank=True)
    public = models.BooleanField(default=True)
    allow_ai_indexing = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["slug"]),
            models.Index(fields=["public", "allow_ai_indexing"]),
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self.build_unique_slug()
        super().save(*args, **kwargs)

    def build_unique_slug(self) -> str:
        base_slug = slugify(self.name) or "company"
        slug = base_slug
        counter = 2
        while Organization.objects.exclude(pk=self.pk).filter(slug=slug).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1
        return slug

    def get_subscription(self):
        from apps.subscriptions.models import Subscription

        subscription, _ = Subscription.objects.get_or_create(organization=self)
        return subscription

    @property
    def subscription_tier(self) -> str:
        return self.get_subscription().tier

    @property
    def supports_advanced_formats(self) -> bool:
        return self.get_subscription().supports("advanced_formats")

    @property
    def supports_llms_txt(self) -> bool:
        return self.get_subscription().supports("llms_txt")

    def localized_text(self, field_prefix: str, language_code: str | None = None) -> str:
        language = (language_code or self.primary_language or "en")[:2]
        fallback = "pl" if language == "en" else "en"
        primary_value = getattr(self, f"{field_prefix}_{language}", "")
        fallback_value = getattr(self, f"{field_prefix}_{fallback}", "")
        return primary_value or fallback_value or ""


class SocialNetwork(models.TextChoices):
    FACEBOOK = "facebook", "Facebook"
    INSTAGRAM = "instagram", "Instagram"
    LINKEDIN = "linkedin", "LinkedIn"
    X = "x", "X"
    TIKTOK = "tiktok", "TikTok"
    YOUTUBE = "youtube", "YouTube"


class SocialProfile(models.Model):
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="social_profiles",
    )
    network = models.CharField(max_length=32, choices=SocialNetwork.choices)
    url = models.URLField()

    class Meta:
        ordering = ["network"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "network"],
                name="unique_network_per_organization",
            )
        ]

    def __str__(self) -> str:
        return f"{self.organization.name} - {self.network}"


class Tag(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="tags")
    name = models.CharField(max_length=80)
    language = models.CharField(max_length=2, choices=settings.LANGUAGES, blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name", "language"],
                name="unique_tag_per_language",
            )
        ]

    def __str__(self) -> str:
        return self.name


class Product(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="products")
    name = models.CharField(max_length=255)
    short_description_en = models.CharField(max_length=280, blank=True)
    short_description_pl = models.CharField(max_length=280, blank=True)
    product_url = models.URLField(blank=True)
    price_from = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    currency = models.CharField(max_length=3, default="PLN")
    is_featured = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_featured", "name"]

    def __str__(self) -> str:
        return self.name

    def localized_summary(self, language_code: str | None = None) -> str:
        language = (language_code or self.organization.primary_language or "en")[:2]
        fallback = "pl" if language == "en" else "en"
        primary_value = getattr(self, f"short_description_{language}", "")
        fallback_value = getattr(self, f"short_description_{fallback}", "")
        return primary_value or fallback_value or ""


class EntryType(models.TextChoices):
    UPDATE = "update", "Update"
    FAQ = "faq", "FAQ"
    GUIDE = "guide", "Guide"
    CASE_STUDY = "case-study", "Case study"


class ContentEntry(models.Model):
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="content_entries",
    )
    entry_type = models.CharField(max_length=32, choices=EntryType.choices, default=EntryType.UPDATE)
    title = models.CharField(max_length=255)
    summary_en = models.CharField(max_length=280, blank=True)
    summary_pl = models.CharField(max_length=280, blank=True)
    content_url = models.URLField(blank=True)
    published_at = models.DateTimeField(default=timezone.now)
    is_featured = models.BooleanField(default=False)

    class Meta:
        ordering = ["-published_at"]

    def __str__(self) -> str:
        return self.title

    def localized_summary(self, language_code: str | None = None) -> str:
        language = (language_code or self.organization.primary_language or "en")[:2]
        fallback = "pl" if language == "en" else "en"
        primary_value = getattr(self, f"summary_{language}", "")
        fallback_value = getattr(self, f"summary_{fallback}", "")
        return primary_value or fallback_value or ""
