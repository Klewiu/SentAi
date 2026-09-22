from django.db import migrations, models


def copy_existing_entries(apps, schema_editor):
    ContentEntry = apps.get_model("companies", "ContentEntry")
    for entry in ContentEntry.objects.using(schema_editor.connection.alias).iterator():
        questions = {}
        answers = {}
        if entry.summary_en:
            questions["en"] = entry.title
            answers["en"] = entry.summary_en
        if entry.summary_pl:
            questions["pl"] = entry.title
            answers["pl"] = entry.summary_pl
        entry.questions_by_language = questions
        entry.answers_by_language = answers
        entry.save(update_fields=["questions_by_language", "answers_by_language"])


class Migration(migrations.Migration):
    dependencies = [("companies", "0016_product_names_by_language")]

    operations = [
        migrations.AddField(
            model_name="contententry",
            name="questions_by_language",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="contententry",
            name="answers_by_language",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.RunPython(copy_existing_entries, migrations.RunPython.noop),
    ]
