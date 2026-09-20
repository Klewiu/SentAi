from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify
import json
import uuid


class OrganizationType(models.TextChoices):
    MANUFACTURING = "manufacturing", "Manufacturing"
    SERVICES = "services", "Services"
    TRADING = "trading", "Trading"
    OTHER = "other", "Other"


class SourceType(models.TextChoices):
    USER_SUBMITTED = "user_submitted", "User submitted"


class VerificationStatus(models.TextChoices):
    UNVERIFIED = "unverified", "Unverified"
    HUMAN_ADMIN_VERIFIED = "human_admin_verified", "Human admin verified"


class Organization(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="organizations",
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    company_type = models.CharField(max_length=32, choices=OrganizationType.choices, default=OrganizationType.OTHER)
    website_url = models.URLField(blank=True)
    contact_email = models.EmailField(max_length=254, blank=True)
    phone_number = models.CharField(max_length=64, blank=True)
    address_line = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=120, blank=True)
    postal_code = models.CharField(max_length=24, blank=True)
    country = models.CharField(max_length=120, blank=True)
    ai_summary = models.CharField(max_length=280, blank=True)
    source_type = models.CharField(max_length=32, choices=SourceType.choices, default=SourceType.USER_SUBMITTED)
    source_url = models.URLField(blank=True)
    verification_status = models.CharField(
        max_length=32,
        choices=VerificationStatus.choices,
        default=VerificationStatus.UNVERIFIED,
    )
    verified_at = models.DateTimeField(blank=True, null=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="verified_organizations",
        blank=True,
        null=True,
    )
    last_reviewed_at = models.DateTimeField(blank=True, null=True)
    last_reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="reviewed_organizations",
        blank=True,
        null=True,
    )
    primary_language = models.CharField(
        max_length=2,
        choices=getattr(settings, "FEED_LANGUAGES", settings.LANGUAGES),
        default="pl",
    )
    content_languages = models.JSONField(default=list, help_text="Selected content languages for this organization")
    descriptions_by_language = models.JSONField(default=dict, blank=True)
    products_by_language = models.JSONField(default=dict, blank=True)
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
        self.source_type = SourceType.USER_SUBMITTED
        self.source_url = self.website_url or ""
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
        from apps.billing.access import effective_tier
        # A value object: public reads never create or mutate billing records.
        return Subscription(organization=self, tier=effective_tier(self.owner))

    @property
    def subscription_tier(self) -> str:
        return self.get_subscription().tier

    @property
    def is_verified(self) -> bool:
        return self.verification_status == VerificationStatus.HUMAN_ADMIN_VERIFIED

    @property
    def supports_advanced_formats(self) -> bool:
        return self.get_subscription().supports("advanced_formats")

    @property
    def supports_llms_txt(self) -> bool:
        return self.get_subscription().supports("llms_txt")

    @property
    def supports_company_md(self) -> bool:
        return self.get_subscription().supports("company_md")

    def localized_text(self, field_prefix: str, language_code: str | None = None) -> str:
        language = (language_code or self.primary_language or "en")[:2]
        fallback = "pl" if language == "en" else "en"
        language_payload = self.descriptions_by_language or {}
        field_key = "short" if field_prefix == "short_description" else "long"

        primary_payload = language_payload.get(language, {})
        fallback_payload = language_payload.get(fallback, {})
        primary_json_value = (primary_payload.get(field_key) or "").strip()
        fallback_json_value = (fallback_payload.get(field_key) or "").strip()

        if primary_json_value:
            return primary_json_value
        if fallback_json_value:
            return fallback_json_value

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
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    names_by_language = models.JSONField(default=dict, blank=True)
    descriptions_by_language = models.JSONField(default=dict, blank=True)
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

    def translation_for_editor(self, code):
        names = self.names_by_language or {}
        name = names.get(code, self.name if code == self.organization.primary_language and not names else "")
        descriptions = self.descriptions_by_language or {}
        description = descriptions.get(code, getattr(self, f"short_description_{code}", ""))
        return {"name": name, "description": description, "url": self.product_url}

    def localized_name(self, code):
        return (self.names_by_language or {}).get(code) or self.name

    def translation_in(self, language_code: str) -> dict | None:
        """Return content authored in this language, without cross-language fallback."""
        language = (language_code or self.organization.primary_language or "en")[:2]
        names = self.names_by_language or {}
        descriptions = self.descriptions_by_language or {}
        name = (names.get(language) or "").strip()
        description = (descriptions.get(language) or "").strip()
        if not description and hasattr(self, f"short_description_{language}"):
            description = (getattr(self, f"short_description_{language}", "") or "").strip()
        if not name and language == self.organization.primary_language:
            name = (self.name or "").strip()
        if not name:
            return None
        return {"name": name, "description": description, "url": self.product_url}

    def localized_summary(self, language_code: str | None = None) -> str:
        language = (language_code or self.organization.primary_language or "en")[:2]
        if self.descriptions_by_language.get(language):
            return self.descriptions_by_language[language]
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
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="content_entries",
    )
    entry_type = models.CharField(max_length=32, choices=EntryType.choices, default=EntryType.UPDATE)
    title = models.CharField(max_length=255)
    questions_by_language = models.JSONField(default=dict, blank=True)
    answers_by_language = models.JSONField(default=dict, blank=True)
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
        return self.localized_answer(language_code)

    def localized_question(self, language_code: str | None = None) -> str:
        language = (language_code or self.organization.primary_language or "en")[:2]
        questions = self.questions_by_language or {}
        return questions.get(language) or questions.get(self.organization.primary_language) or self.title

    def localized_answer(self, language_code: str | None = None) -> str:
        language = (language_code or self.organization.primary_language or "en")[:2]
        answers = self.answers_by_language or {}
        if answers.get(language):
            return answers[language]
        if answers.get(self.organization.primary_language):
            return answers[self.organization.primary_language]
        fallback = "pl" if language == "en" else "en"
        primary_value = getattr(self, f"summary_{language}", "")
        fallback_value = getattr(self, f"summary_{fallback}", "")
        return primary_value or fallback_value or ""

    def translation_for_editor(self, language_code: str) -> dict:
        return {
            "id": self.pk,
            "question": (self.questions_by_language or {}).get(language_code, ""),
            "answer": (self.answers_by_language or {}).get(language_code, ""),
            "url": self.content_url,
        }

    def translation_in(self, language_code: str) -> dict | None:
        """Return content authored in this language, without cross-language fallback."""
        language = (language_code or self.organization.primary_language or "en")[:2]
        question = ((self.questions_by_language or {}).get(language) or "").strip()
        answer = ((self.answers_by_language or {}).get(language) or "").strip()
        if language == self.organization.primary_language:
            question = question or (self.title or "").strip()
            if not answer and hasattr(self, f"summary_{language}"):
                answer = (getattr(self, f"summary_{language}", "") or "").strip()
        if not question or (self.entry_type == EntryType.FAQ and not answer):
            return None
        return {"question": question, "answer": answer, "url": self.content_url}


class Page(models.Model):
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="pages",
    )
    name = models.CharField(max_length=255)
    url = models.URLField()
    verification_status = models.CharField(
        max_length=32,
        choices=VerificationStatus.choices,
        default=VerificationStatus.UNVERIFIED,
    )
    verified_at = models.DateTimeField(blank=True, null=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_pages",
    )

    def __str__(self):
        return self.name
