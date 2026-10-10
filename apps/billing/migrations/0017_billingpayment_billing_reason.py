from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0016_billingsubscription_termination_acknowledged_at_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="billingpayment",
            name="billing_reason",
            field=models.CharField(blank=True, max_length=64),
        ),
    ]
