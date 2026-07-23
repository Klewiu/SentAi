from django.contrib import admin

from .models import AdminNotification, CustomerNotification


@admin.register(AdminNotification)
class AdminNotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "severity", "customer", "closed_at", "closed_by", "created_at")
    list_filter = ("category", "severity", "closed_at")
    search_fields = ("title", "message", "customer__email", "reference_key")
    readonly_fields = ("created_at", "updated_at")


@admin.register(CustomerNotification)
class CustomerNotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "severity", "user", "closed_at", "created_at")
    list_filter = ("category", "severity", "closed_at")
    search_fields = ("title", "message", "user__email", "reference_key")
    readonly_fields = ("created_at", "updated_at")
