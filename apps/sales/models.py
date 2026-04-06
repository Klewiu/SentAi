from django.db import models
from django.contrib.auth import get_user_model


User = get_user_model()


class ProspectClient(models.Model):
    """Potencjalny klient (prospekt) dodany przez sprzedawcę."""
    seller = models.ForeignKey(User, on_delete=models.CASCADE, related_name="prospect_clients")
    registered_client = models.OneToOneField(
        User,
        on_delete=models.SET_NULL,
        related_name="attributed_prospect",
        null=True,
        blank=True,
    )
    company_name = models.CharField(max_length=255)
    contact_person = models.CharField(max_length=255)
    email = models.EmailField()
    phone = models.CharField(max_length=20)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.company_name} ({self.seller.username})"


class ProspectActivity(models.Model):
    """Aktywność (uwagi, notatki) dotycząca prospektu."""
    ACTIVITY_TYPE_CHOICES = [
        ("call", "Telefon"),
        ("email", "Email"),
        ("meeting", "Spotkanie"),
    ]

    prospect = models.ForeignKey(
        ProspectClient,
        on_delete=models.CASCADE,
        related_name="activities"
    )
    seller = models.ForeignKey(User, on_delete=models.CASCADE, related_name="prospect_activities")
    activity_type = models.CharField(max_length=10, choices=ACTIVITY_TYPE_CHOICES, default="call")
    activity_date = models.DateField()
    activity_description = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-activity_date", "-created_at"]

    def __str__(self) -> str:
        return f"{self.prospect.company_name} - {self.activity_date}"


class SellerSettlement(models.Model):
    seller = models.ForeignKey(User, on_delete=models.CASCADE, related_name="seller_settlements")
    client = models.OneToOneField(User, on_delete=models.CASCADE, related_name="seller_settlement")
    prospect = models.ForeignKey(
        ProspectClient,
        on_delete=models.SET_NULL,
        related_name="settlements",
        null=True,
        blank=True,
    )
    client_registered_at = models.DateTimeField()
    client_paid_plan_started_at = models.DateTimeField(blank=True, null=True)
    client_plan_tier = models.CharField(max_length=16)
    settled_at = models.DateTimeField(auto_now_add=True)
    settled_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="processed_seller_settlements",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-settled_at"]

    def __str__(self) -> str:
        return f"{self.client.email} settled for {self.seller.username}"
