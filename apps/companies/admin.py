from django.contrib import admin
from django.utils import timezone

from .models import ContentEntry, Organization, Product, SocialProfile, Tag, VerificationStatus


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "owner",
        "company_type",
        "verification_status",
        "verified_at",
        "slug",
        "public",
        "allow_ai_indexing",
        "updated_at",
    )
    list_filter = ("company_type", "verification_status", "public", "allow_ai_indexing", "primary_language")
    search_fields = ("name", "slug", "owner__email")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("source_type", "source_url", "verified_at", "verified_by")
    actions = ("mark_as_human_admin_verified", "mark_as_unverified")

    @admin.action(description="Mark selected organizations as human admin verified")
    def mark_as_human_admin_verified(self, request, queryset):
        for organization in queryset:
            organization.verification_status = VerificationStatus.HUMAN_ADMIN_VERIFIED
            if organization.verified_at is None:
                organization.verified_at = timezone.now()
            if organization.verified_by_id is None:
                organization.verified_by = request.user
            organization.save(update_fields=["verification_status", "verified_at", "verified_by", "updated_at"])

    @admin.action(description="Mark selected organizations as unverified")
    def mark_as_unverified(self, request, queryset):
        queryset.update(
            verification_status=VerificationStatus.UNVERIFIED,
            verified_at=None,
            verified_by=None,
        )


@admin.register(SocialProfile)
class SocialProfileAdmin(admin.ModelAdmin):
    list_display = ("organization", "network", "url")
    list_filter = ("network",)
    search_fields = ("organization__name", "url")


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "language")
    list_filter = ("language",)
    search_fields = ("name", "organization__name")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "price_from", "currency", "is_featured")
    list_filter = ("currency", "is_featured")
    search_fields = ("name", "organization__name")


@admin.register(ContentEntry)
class ContentEntryAdmin(admin.ModelAdmin):
    list_display = ("title", "organization", "entry_type", "published_at", "is_featured")
    list_filter = ("entry_type", "is_featured")
    search_fields = ("title", "organization__name")
