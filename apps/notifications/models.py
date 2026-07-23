from django.conf import settings
from django.db import models
from django.urls import reverse


class NotificationSeverity(models.TextChoices):
    INFO = "info", "Info"
    SUCCESS = "success", "Success"
    WARNING = "warning", "Warning"
    URGENT = "urgent", "Urgent"


class NotificationCategory(models.TextChoices):
    CUSTOMER = "customer", "Customer"
    PLAN = "plan", "Plan"
    PAYMENT = "payment", "Payment"
    INVOICE = "invoice", "Invoice"
    MANUAL_PLAN = "manual_plan", "Pro Manual"
    STRIPE = "stripe", "Stripe"


class AdminNotification(models.Model):
    title = models.CharField(max_length=180)
    message = models.TextField()
    title_pl = models.CharField(max_length=180, blank=True)
    message_pl = models.TextField(blank=True)
    category = models.CharField(max_length=32, choices=NotificationCategory.choices)
    severity = models.CharField(max_length=16, choices=NotificationSeverity.choices, default=NotificationSeverity.INFO)
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="admin_notifications",
    )
    action_url = models.CharField(max_length=255, blank=True)
    reference_key = models.CharField(max_length=180, unique=True, blank=True, null=True)
    closed_at = models.DateTimeField(blank=True, null=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="closed_admin_notifications",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["closed_at", "-created_at"]
        indexes = [
            models.Index(fields=["closed_at", "-created_at"]),
            models.Index(fields=["category", "closed_at"]),
            models.Index(fields=["severity", "closed_at"]),
        ]

    def __str__(self):
        return self.title

    @property
    def is_closed(self) -> bool:
        return self.closed_at is not None

    def get_absolute_url(self):
        return reverse("dashboard:notifications")

    def localized_title(self, language_code):
        return self.title_pl if language_code == "pl" and self.title_pl else self.title

    def localized_message(self, language_code):
        return self.message_pl if language_code == "pl" and self.message_pl else self.message


class CustomerNotification(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="customer_notifications")
    title = models.CharField(max_length=180)
    message = models.TextField()
    title_pl = models.CharField(max_length=180, blank=True)
    message_pl = models.TextField(blank=True)
    category = models.CharField(max_length=32, choices=NotificationCategory.choices)
    severity = models.CharField(max_length=16, choices=NotificationSeverity.choices, default=NotificationSeverity.INFO)
    action_url = models.CharField(max_length=255, blank=True)
    reference_key = models.CharField(max_length=180, unique=True, blank=True, null=True)
    closed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["closed_at", "-created_at"]
        indexes = [
            models.Index(fields=["user", "closed_at", "-created_at"]),
            models.Index(fields=["user", "category", "closed_at"]),
            models.Index(fields=["user", "severity", "closed_at"]),
        ]

    def __str__(self):
        return f"{self.user} - {self.title}"

    @property
    def is_closed(self) -> bool:
        return self.closed_at is not None

    def get_absolute_url(self):
        return reverse("dashboard:customer-notifications")

    def localized_title(self, language_code):
        return self.title_pl if language_code == "pl" and self.title_pl else self.title

    def localized_message(self, language_code):
        return self.message_pl if language_code == "pl" and self.message_pl else self.message
