from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone

from apps.accounts.models import UserPlanTier


class BillingInterval(models.TextChoices):
    YEAR = "year", "Year"


class BillingCurrency(models.TextChoices):
    PLN = "pln", "PLN"
    EUR = "eur", "EUR"


class BillingSubscriptionStatus(models.TextChoices):
    INCOMPLETE = "incomplete", "Incomplete"
    INCOMPLETE_EXPIRED = "incomplete_expired", "Incomplete expired"
    TRIALING = "trialing", "Trialing"
    ACTIVE = "active", "Active"
    PAST_DUE = "past_due", "Past due"
    CANCELED = "canceled", "Canceled"
    UNPAID = "unpaid", "Unpaid"
    PAUSED = "paused", "Paused"


class BillingPaymentStatus(models.TextChoices):
    PAID = "paid", "Paid"
    OPEN = "open", "Open"
    VOID = "void", "Void"
    UNCOLLECTIBLE = "uncollectible", "Uncollectible"
    FAILED = "failed", "Failed"


class BillingCustomerType(models.TextChoices):
    PERSON = "person", "Person"
    COMPANY = "company", "Company"


class ManualPlanOrderStatus(models.TextChoices):
    AWAITING_PAYMENT = "awaiting_payment", "Awaiting payment"
    PAID = "paid", "Paid"
    DISABLED = "disabled", "Disabled"


class BillingProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="billing_profile",
    )
    customer_type = models.CharField(
        max_length=16,
        choices=BillingCustomerType.choices,
        default=BillingCustomerType.COMPANY,
    )
    company_name = models.CharField(max_length=255, blank=True)
    tax_id = models.CharField(max_length=64)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    street = models.CharField(max_length=255)
    postal_code = models.CharField(max_length=32)
    city = models.CharField(max_length=120)
    country = models.CharField(max_length=2, default="PL")
    invoice_email = models.EmailField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user__email"]

    def __str__(self) -> str:
        return f"{self.user} billing profile"

    def billing_currency(self) -> str:
        return BillingCurrency.PLN if self.country.upper() == "PL" else BillingCurrency.EUR

    def is_complete(self) -> bool:
        if self.customer_type != BillingCustomerType.COMPANY:
            return False
        if not self.company_name:
            return False
        if not self.tax_id:
            return False
        return all([self.street, self.postal_code, self.city, self.country, self.invoice_email])


class BillingPlanPrice(models.Model):
    tier = models.CharField(max_length=16, choices=UserPlanTier.choices)
    stripe_price_id = models.CharField(max_length=255, unique=True)
    amount = models.PositiveIntegerField(help_text="Amount in the smallest currency unit, e.g. grosz/cents.")
    currency = models.CharField(max_length=8, choices=BillingCurrency.choices, default=BillingCurrency.PLN)
    interval = models.CharField(max_length=16, choices=BillingInterval.choices, default=BillingInterval.YEAR)
    active_for_new_customers = models.BooleanField(default=True)
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="created_billing_prices",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tier", "currency", "-active_for_new_customers", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tier", "currency"],
                condition=models.Q(active_for_new_customers=True),
                name="one_active_billing_price_per_tier_currency",
            )
        ]

    def __str__(self) -> str:
        state = "active" if self.active_for_new_customers else "archived"
        return f"{self.tier} {self.formatted_amount()} / {self.interval} ({state})"

    def formatted_amount(self) -> str:
        return f"{self.amount / 100:.2f} {self.currency.upper()}"


class BillingSubscription(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="billing_subscription",
    )
    tier = models.CharField(max_length=16, choices=UserPlanTier.choices)
    plan_price = models.ForeignKey(
        BillingPlanPrice,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="subscriptions",
    )
    stripe_customer_id = models.CharField(max_length=255, blank=True)
    stripe_subscription_id = models.CharField(max_length=255, blank=True)
    stripe_price_id = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=32,
        choices=BillingSubscriptionStatus.choices,
        default=BillingSubscriptionStatus.INCOMPLETE,
    )
    current_period_start = models.DateTimeField(blank=True, null=True)
    current_period_end = models.DateTimeField(blank=True, null=True)
    cancel_at_period_end = models.BooleanField(default=False)
    canceled_at = models.DateTimeField(blank=True, null=True)
    latest_invoice_id = models.CharField(max_length=255, blank=True)
    latest_payment_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return f"{self.user} - {self.tier} - {self.status}"

    @property
    def is_active_for_access(self) -> bool:
        return self.status in {BillingSubscriptionStatus.ACTIVE, BillingSubscriptionStatus.TRIALING}


class ManualPlanOrder(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="manual_plan_orders")
    tier = models.CharField(max_length=16, choices=UserPlanTier.choices, default=UserPlanTier.PRO)
    amount = models.PositiveIntegerField(help_text="Amount in the smallest currency unit.")
    currency = models.CharField(max_length=8, choices=BillingCurrency.choices)
    status = models.CharField(
        max_length=32,
        choices=ManualPlanOrderStatus.choices,
        default=ManualPlanOrderStatus.AWAITING_PAYMENT,
    )
    payment_reference = models.CharField(max_length=255, unique=True)
    payment_due_at = models.DateTimeField()
    access_until = models.DateTimeField()
    paid_at = models.DateTimeField(blank=True, null=True)
    disabled_at = models.DateTimeField(blank=True, null=True)
    disabled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="disabled_manual_plan_orders",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.payment_reference} - {self.user} - {self.status}"

    def formatted_amount(self) -> str:
        return f"{self.amount / 100:.2f} {self.currency.upper()}"

    @property
    def is_overdue(self) -> bool:
        return self.status == ManualPlanOrderStatus.AWAITING_PAYMENT and timezone.now() > self.payment_due_at


class BillingPayment(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="billing_payments")
    subscription = models.ForeignKey(
        BillingSubscription,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="payments",
    )
    stripe_invoice_id = models.CharField(max_length=255, unique=True)
    stripe_payment_intent_id = models.CharField(max_length=255, blank=True)
    amount_paid = models.PositiveIntegerField(default=0)
    currency = models.CharField(max_length=8, default="pln")
    status = models.CharField(max_length=32, choices=BillingPaymentStatus.choices, default=BillingPaymentStatus.OPEN)
    paid_at = models.DateTimeField(blank=True, null=True)
    hosted_invoice_url = models.URLField(blank=True)
    invoice_pdf = models.URLField(blank=True)
    invoice_issued = models.BooleanField(default=False)
    invoice_issued_at = models.DateField(blank=True, null=True)
    invoice_sent = models.BooleanField(default=False)
    invoice_sent_at = models.DateField(blank=True, null=True)
    invoice_number = models.CharField(max_length=100, blank=True)
    invoice_document = models.FileField(
        upload_to="invoices/%Y/%m/",
        blank=True,
        validators=[FileExtensionValidator(["pdf"])],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-paid_at", "-created_at"]

    def __str__(self) -> str:
        return f"{self.user} - {self.amount_paid / 100:.2f} {self.currency.upper()} - {self.status}"

    def formatted_amount(self) -> str:
        return f"{self.amount_paid / 100:.2f} {self.currency.upper()}"


class BillingInvoice(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="billing_invoices")
    subscription = models.ForeignKey(
        BillingSubscription,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="invoices",
    )
    manual_order = models.ForeignKey(
        ManualPlanOrder,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="invoices",
    )
    payment = models.ForeignKey(
        BillingPayment,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="invoices",
    )
    invoice_number = models.CharField(max_length=100)
    issued_at = models.DateField()
    document = models.FileField(
        upload_to="invoices/%Y/%m/",
        validators=[FileExtensionValidator(["pdf"])],
    )
    sent = models.BooleanField(default=False)
    sent_at = models.DateField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-issued_at", "-created_at"]

    def __str__(self):
        return f"{self.invoice_number} - {self.user}"
