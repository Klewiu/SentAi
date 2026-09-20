import uuid

from django.db import migrations, models


def populate_public_ids(apps, schema_editor):
    for model_name in ("Organization", "Product", "ContentEntry"):
        Model = apps.get_model("companies", model_name)
        for item in Model.objects.using(schema_editor.connection.alias).all().iterator():
            item.public_id = uuid.uuid4()
            item.save(update_fields=["public_id"])


class Migration(migrations.Migration):
    dependencies = [("companies", "0017_contententry_multilingual_faq")]

    operations = [
        migrations.AddField(
            model_name="organization",
            name="public_id",
            field=models.UUIDField(null=True, editable=False),
        ),
        migrations.AddField(
            model_name="product",
            name="public_id",
            field=models.UUIDField(null=True, editable=False),
        ),
        migrations.AddField(
            model_name="contententry",
            name="public_id",
            field=models.UUIDField(null=True, editable=False),
        ),
        migrations.RunPython(populate_public_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="organization",
            name="public_id",
            field=models.UUIDField(default=uuid.uuid4, unique=True, editable=False),
        ),
        migrations.AlterField(
            model_name="product",
            name="public_id",
            field=models.UUIDField(default=uuid.uuid4, unique=True, editable=False),
        ),
        migrations.AlterField(
            model_name="contententry",
            name="public_id",
            field=models.UUIDField(default=uuid.uuid4, unique=True, editable=False),
        ),
    ]
