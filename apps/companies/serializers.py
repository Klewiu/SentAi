from rest_framework import serializers

from .models import ContentEntry, Organization, Product, SocialProfile, Tag
from .services import public_feed_urls


class OrganizationSerializer(serializers.ModelSerializer):
    subscription_tier = serializers.SerializerMethodField()
    feature_matrix = serializers.SerializerMethodField()
    public_urls = serializers.SerializerMethodField()

    class Meta:
        model = Organization
        fields = (
            "id",
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
            "primary_language",
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
        )
        read_only_fields = ("id", "owner", "subscription_tier", "feature_matrix", "public_urls", "created_at", "updated_at")
        extra_kwargs = {
            "slug": {"required": False, "allow_blank": True},
        }

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

    class Meta:
        model = Product
        fields = (
            "id",
            "organization",
            "name",
            "short_description_en",
            "short_description_pl",
            "product_url",
            "price_from",
            "currency",
            "is_featured",
            "created_at",
        )
        read_only_fields = ("id", "organization", "created_at")


class ContentEntrySerializer(PlanLimitedModelSerializer):
    limit_key = "content_entries"
    related_name = "content_entries"
    resource_label = "content entries"

    class Meta:
        model = ContentEntry
        fields = (
            "id",
            "organization",
            "entry_type",
            "title",
            "summary_en",
            "summary_pl",
            "content_url",
            "published_at",
            "is_featured",
        )
        read_only_fields = ("id", "organization")
