from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0008_billinginvoice"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ManualPlanOrder",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("tier", models.CharField(choices=[("BASIC", "Basic"), ("PLUS", "Plus"), ("PRO", "Pro")], default="PRO", max_length=16)),
                ("amount", models.PositiveIntegerField(help_text="Amount in the smallest currency unit.")),
                ("currency", models.CharField(choices=[("pln", "PLN"), ("eur", "EUR")], max_length=8)),
                ("status", models.CharField(choices=[("awaiting_payment", "Awaiting payment"), ("paid", "Paid"), ("disabled", "Disabled")], default="awaiting_payment", max_length=32)),
                ("payment_reference", models.CharField(max_length=255, unique=True)),
                ("payment_due_at", models.DateTimeField()),
                ("access_until", models.DateTimeField()),
                ("paid_at", models.DateTimeField(blank=True, null=True)),
                ("disabled_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("disabled_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="disabled_manual_plan_orders", to=settings.AUTH_USER_MODEL)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="manual_plan_orders", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
