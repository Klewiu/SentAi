from django.contrib import admin

from .models import ContentEntry, Organization, Product, SocialProfile, Tag


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "company_type", "slug", "public", "allow_ai_indexing", "updated_at")
    list_filter = ("company_type", "public", "allow_ai_indexing", "primary_language")
    search_fields = ("name", "slug", "contact_email")
    prepopulated_fields = {"slug": ("name",)}


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
