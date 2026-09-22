from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0011_user_closed_display_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="plan_access_status",
            field=models.CharField(
                choices=[("ACTIVE", "Active"), ("EXPIRED", "Expired")],
                default="EXPIRED",
                max_length=16,
            ),
        ),
    ]