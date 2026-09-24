from django.contrib import admin
from django import forms
from django.db import models

from .models import BillingInvoice, BillingPayment, BillingPlanPrice, BillingProfile, BillingSubscription, ManualPlanOrder


@admin.register(ManualPlanOrder)
class ManualPlanOrderAdmin(admin.ModelAdmin):
    list_display = ("payment_reference", "user", "amount", "currency", "status", "payment_due_at", "access_until")
    list_filter = ("status", "currency")
    search_fields = ("payment_reference", "user__email", "user__username")


@admin.register(BillingPlanPrice)
class BillingPlanPriceAdmin(admin.ModelAdmin):
    list_display = ("tier", "formatted_amount", "currency", "interval", "active_for_new_customers", "stripe_price_id")
    list_filter = ("tier", "active_for_new_customers", "currency", "interval")
    search_fields = ("stripe_price_id", "notes")
    readonly_fields = (
        "tier", "stripe_price_id", "amount", "currency", "interval",
        "active_for_new_customers", "notes", "created_by", "created_at", "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BillingSubscription)
class BillingSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "tier", "status", "cancel_at_period_end", "current_period_end", "stripe_subscription_id")
    list_filter = ("tier", "status", "cancel_at_period_end")
    search_fields = ("user__email", "user__username", "stripe_customer_id", "stripe_subscription_id")


@admin.register(BillingPayment)
class BillingPaymentAdmin(admin.ModelAdmin):
    formfield_overrides = {models.FileField: {"widget": forms.FileInput}}
    list_display = ("user", "amount_paid", "currency", "status", "paid_at", "invoice_issued", "invoice_issued_at", "invoice_sent", "invoice_sent_at", "invoice_number", "stripe_invoice_id")
    list_filter = ("status", "currency", "invoice_issued", "invoice_sent")
    search_fields = ("user__email", "user__username", "invoice_number", "stripe_invoice_id", "stripe_payment_intent_id")


@admin.register(BillingProfile)
class BillingProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "customer_type", "company_name", "tax_id", "country", "invoice_email")
    list_filter = ("customer_type", "country")
    search_fields = ("user__email", "company_name", "tax_id", "invoice_email")


@admin.register(BillingInvoice)
class BillingInvoiceAdmin(admin.ModelAdmin):
    formfield_overrides = {models.FileField: {"widget": forms.FileInput}}
    readonly_fields = ("billing_snapshot",)
    list_display = ("invoice_number", "user", "issued_at", "sent", "sent_at")
    list_filter = ("sent", "issued_at")
    search_fields = ("invoice_number", "user__email", "user__username")
