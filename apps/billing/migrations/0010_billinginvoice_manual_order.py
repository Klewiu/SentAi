from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("billing", "0009_manualplanorder")]

    operations = [
        migrations.AddField(
            model_name="billinginvoice",
            name="manual_order",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="invoices",
                to="billing.manualplanorder",
            ),
        ),
    ]
