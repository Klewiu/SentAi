from django.db import migrations, models


def split_faq_translations(apps, schema_editor):
    ContentEntry = apps.get_model("companies", "ContentEntry")
    database = schema_editor.connection.alias
    entries = list(
        ContentEntry.objects.using(database)
        .filter(entry_type="faq")
        .select_related("organization")
        .order_by("pk")
    )

    for entry in entries:
        primary_language = (entry.organization.primary_language or "en")[:2]
        questions = entry.questions_by_language or {}
        answers = entry.answers_by_language or {}
        languages = list(dict.fromkeys(
            [primary_language]
            + list(questions)
            + list(answers)
            + [
                code for code in ("en", "pl")
                if getattr(entry, f"summary_{code}", "")
            ]
        ))
        translations = []
        for language in languages:
            question = (questions.get(language) or "").strip()
            answer = (
                answers.get(language)
                or getattr(entry, f"summary_{language}", "")
                or ""
            ).strip()
            if language == primary_language:
                question = question or (entry.title or "").strip()
            if question or answer:
                translations.append((language, question, answer))

        for index, (language, question, answer) in enumerate(translations):
            localized_entry = entry
            if index:
                localized_entry = ContentEntry.objects.using(database).create(
                    organization_id=entry.organization_id,
                    entry_type=entry.entry_type,
                    title=question or entry.title,
                    summary_en="",
                    summary_pl="",
                    content_url=entry.content_url,
                    published_at=entry.published_at,
                    is_featured=entry.is_featured,
                )
            localized_entry.language = language
            localized_entry.title = question or entry.title
            localized_entry.questions_by_language = {language: question} if question else {}
            localized_entry.answers_by_language = {language: answer} if answer else {}
            localized_entry.summary_en = answer[:280] if language == "en" else ""
            localized_entry.summary_pl = answer[:280] if language == "pl" else ""
            localized_entry.save(using=database)


class Migration(migrations.Migration):
    dependencies = [("companies", "0019_product_language")]

    operations = [
        migrations.AddField(
            model_name="contententry",
            name="language",
            field=models.CharField(blank=True, default="", max_length=2),
        ),
        migrations.RunPython(split_faq_translations, migrations.RunPython.noop),
    ]
