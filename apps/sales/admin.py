from django.contrib import admin
from .models import ProspectClient, ProspectActivity, SellerSettlement


@admin.register(ProspectClient)
class ProspectClientAdmin(admin.ModelAdmin):
    list_display = (
        "company_name",
        "contact_person",
        "email",
        "phone",
        "seller",
        "registered_client",
        "created_at",
    )
    list_filter = ("seller", "created_at")
    search_fields = ("company_name", "contact_person", "email", "registered_client__email")
    readonly_fields = ("created_at", "updated_at")


@admin.register(ProspectActivity)
class ProspectActivityAdmin(admin.ModelAdmin):
    list_display = ("prospect", "activity_date", "seller", "created_at")
    list_filter = ("seller", "activity_date", "prospect")
    search_fields = ("prospect__company_name", "activity_description")
    readonly_fields = ("created_at",)


@admin.register(SellerSettlement)
class SellerSettlementAdmin(admin.ModelAdmin):
    list_display = ("client", "seller", "client_plan_tier", "settled_at", "settled_by")
    list_filter = ("seller", "client_plan_tier", "settled_at")
    search_fields = ("client__email", "seller__username")
    readonly_fields = ("settled_at",)
