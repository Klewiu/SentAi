from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("billing", "0010_billinginvoice_manual_order")]

    operations = [
        migrations.AddField(
            model_name="billinginvoice",
            name="payment",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="invoices",
                to="billing.billingpayment",
            ),
        ),
    ]
