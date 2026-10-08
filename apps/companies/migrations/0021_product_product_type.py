from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0020_contententry_language"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="product_type",
            field=models.CharField(
                choices=[("product", "Product"), ("service", "Service")],
                default="product",
                max_length=16,
            ),
        ),
    ]
