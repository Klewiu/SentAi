from django.conf import settings
from rest_framework import serializers

from .models import ContentEntry, Organization, Product, SocialProfile, Tag, Page
from .services import public_feed_urls
from .page_serializers import PageSerializer


def clean_text_map(value, field_name, maximum, allowed_languages):
    if not isinstance(value, dict):
        raise serializers.ValidationError({field_name: "Use an object keyed by language code."})
    cleaned = {}
    for code, text in value.items():
        if code not in allowed_languages or not isinstance(text, str):
            raise serializers.ValidationError({field_name: "Use supported language codes and text values."})
        text = text.strip()
        if len(text) > maximum:
            raise serializers.ValidationError({field_name: f"Each value must be at most {maximum} characters."})
        if text:
            cleaned[code] = text
    return cleaned


class OrganizationSerializer(serializers.ModelSerializer):
    subscription_tier = serializers.SerializerMethodField()
    feature_matrix = serializers.SerializerMethodField()
    public_urls = serializers.SerializerMethodField()
    pages = PageSerializer(many=True, read_only=True)

    class Meta:
        model = Organization
        fields = (
            "id",
            "public_id",
            "owner",
            "name",
            "slug",
            "company_type",
            "website_url",
            "contact_email",
            "phone_number",
            "address_line",
            "city",
            "postal_code",
            "country",
            "ai_summary",
            "primary_language",
            "content_languages",
            "descriptions_by_language",
            "short_description_en",
            "short_description_pl",
            "long_description_en",
            "long_description_pl",
            "public",
            "allow_ai_indexing",
            "subscription_tier",
            "feature_matrix",
            "public_urls",
            "created_at",
            "updated_at",
            "pages",
        )
        read_only_fields = ("id", "public_id", "owner", "subscription_tier", "feature_matrix", "public_urls", "created_at", "updated_at")
        extra_kwargs = {
            "slug": {"required": False, "allow_blank": True},
        }

    def validate(self, attrs):
        from apps.subscriptions.models import PLAN_FEATURES
        from apps.billing.access import effective_tier
        user = self.instance.owner if self.instance else self.context["request"].user
        limit = PLAN_FEATURES[effective_tier(user)]["languages"]
        allowed_languages = {
            code for code, _label in getattr(settings, "FEED_LANGUAGES", settings.LANGUAGES)
        }
        descriptions = dict(self.instance.descriptions_by_language or {}) if self.instance else {}
        if "descriptions_by_language" in attrs:
            submitted = attrs["descriptions_by_language"]
            if not isinstance(submitted, dict):
                raise serializers.ValidationError({"descriptions_by_language": "Use an object keyed by language code."})
            descriptions = {}
            for code, translation in submitted.items():
                if code not in allowed_languages or not isinstance(translation, dict):
                    raise serializers.ValidationError({"descriptions_by_language": "Use supported language codes and description objects."})
                short = translation.get("short", "")
                long = translation.get("long", "")
                if not isinstance(short, str) or not isinstance(long, str):
                    raise serializers.ValidationError({"descriptions_by_language": "Descriptions must be text."})
                short, long = short.strip(), long.strip()
                if len(short) > 280 or len(long) > 20000:
                    raise serializers.ValidationError({"descriptions_by_language": "Short descriptions allow 280 characters and detailed descriptions 20,000."})
                if short or long:
                    descriptions[code] = {
                        key: text for key, text in (("short", short), ("long", long)) if text
                    }
        for code in ("en", "pl"):
            for length in ("short", "long"):
                field = f"{length}_description_{code}"
                if field in attrs:
                    descriptions.setdefault(code, {})[length] = attrs[field]
        selected_languages = attrs.get(
            "content_languages",
            self.instance.content_languages if self.instance else list(descriptions),
        )
        if not isinstance(selected_languages, list) or any(
            code not in allowed_languages for code in selected_languages
        ):
            raise serializers.ValidationError({"content_languages": "Use a list of supported language codes."})
        languages = list(dict.fromkeys(selected_languages + [
            code for code, values in descriptions.items() if any(values.values())
        ]))
        if len(languages) > limit:
            raise serializers.ValidationError({"plan": f"Your plan allows {limit} content languages."})
        attrs["descriptions_by_language"] = descriptions
        if "content_languages" in attrs:
            attrs["content_languages"] = list(dict.fromkeys(selected_languages))
        return attrs

    def get_subscription_tier(self, obj):
        return obj.get_subscription().tier

    def get_feature_matrix(self, obj):
        return obj.get_subscription().feature_matrix()

    def get_public_urls(self, obj):
        return public_feed_urls(obj, self.context.get("request"))


class PlanLimitedModelSerializer(serializers.ModelSerializer):
    limit_key = ""
    related_name = ""
    resource_label = "resources"

    def validate(self, attrs):
        attrs = super().validate(attrs)
        organization = self.context["organization"]
        subscription = organization.get_subscription()
        limit = subscription.limit_for(self.limit_key)

        if limit <= 0:
            raise serializers.ValidationError(
                {"plan": f"The {subscription.tier} plan does not include {self.resource_label}."}
            )

        if self.instance is None and getattr(organization, self.related_name).count() >= limit:
            raise serializers.ValidationError(
                {"plan": f"The {subscription.tier} plan allows up to {limit} {self.resource_label}."}
            )

        return attrs


class SocialProfileSerializer(PlanLimitedModelSerializer):
    limit_key = "social_profiles"
    related_name = "social_profiles"
    resource_label = "social profiles"

    class Meta:
        model = SocialProfile
        fields = ("id", "organization", "network", "url")
        read_only_fields = ("id", "organization")


class TagSerializer(PlanLimitedModelSerializer):
    limit_key = "tags"
    related_name = "tags"
    resource_label = "tags"

    class Meta:
        model = Tag
        fields = ("id", "organization", "name", "language")
        read_only_fields = ("id", "organization")


class ProductSerializer(PlanLimitedModelSerializer):
    limit_key = "products"
    related_name = "products"
    resource_label = "products"

    def validate(self, attrs):
        attrs = super().validate(attrs)
        from .services import profile_language_choices
        allowed_languages = {
            item["code"] for item in profile_language_choices(self.context["organization"])
        }
        if "names_by_language" in attrs:
            attrs["names_by_language"] = clean_text_map(
                attrs["names_by_language"], "names_by_language", 255, allowed_languages
            )
        if "descriptions_by_language" in attrs:
            attrs["descriptions_by_language"] = clean_text_map(
                attrs["descriptions_by_language"],
                "descriptions_by_language",
                280,
                allowed_languages,
            )
        descriptions = dict(self.instance.descriptions_by_language or {}) if self.instance else {}
        descriptions.update(attrs.get("descriptions_by_language", {}))
        for code in ("en", "pl"):
            field = f"short_description_{code}"
            if field in attrs:
                descriptions[code] = attrs[field]
        attrs["descriptions_by_language"] = descriptions
        return attrs

    class Meta:
        model = Product
        fields = (
            "id",
            "public_id",
            "organization",
            "name",
            "names_by_language",
            "descriptions_by_language",
            "short_description_en",
            "short_description_pl",
            "product_url",
            "price_from",
            "currency",
            "is_featured",
            "created_at",
        )
        read_only_fields = ("id", "public_id", "organization", "created_at")


class ContentEntrySerializer(PlanLimitedModelSerializer):
    limit_key = "content_entries"
    related_name = "content_entries"
    resource_label = "content entries"

    def validate(self, attrs):
        attrs = super().validate(attrs)
        from .services import profile_language_choices
        allowed_languages = {
            item["code"] for item in profile_language_choices(self.context["organization"])
        }
        for field_name, maximum in (("questions_by_language", 255), ("answers_by_language", 2000)):
            if field_name not in attrs:
                continue
            attrs[field_name] = clean_text_map(
                attrs[field_name], field_name, maximum, allowed_languages
            )

        if attrs.get("entry_type", getattr(self.instance, "entry_type", None)) == "faq" and (
            "questions_by_language" in attrs or "answers_by_language" in attrs
        ):
            questions = attrs.get("questions_by_language", getattr(self.instance, "questions_by_language", {}))
            answers = attrs.get("answers_by_language", getattr(self.instance, "answers_by_language", {}))
            if set(questions) != set(answers):
                raise serializers.ValidationError(
                    {"translations": "Each FAQ translation must include both a question and an answer."}
                )
        return attrs

    class Meta:
        model = ContentEntry
        fields = (
            "id",
            "public_id",
            "organization",
            "entry_type",
            "title",
            "questions_by_language",
            "answers_by_language",
            "summary_en",
            "summary_pl",
            "content_url",
            "published_at",
            "is_featured",
        )
        read_only_fields = ("id", "public_id", "organization")
