from django.db import migrations
from django.utils.text import slugify


POLISH_SLUG_TRANSLATION = str.maketrans({
    "Ą": "A",
    "Ć": "C",
    "Ę": "E",
    "Ł": "L",
    "Ń": "N",
    "Ó": "O",
    "Ś": "S",
    "Ź": "Z",
    "Ż": "Z",
    "ą": "a",
    "ć": "c",
    "ę": "e",
    "ł": "l",
    "ń": "n",
    "ó": "o",
    "ś": "s",
    "ź": "z",
    "ż": "z",
})


def transliterate_generated_slugs(apps, schema_editor):
    Organization = apps.get_model("companies", "Organization")
    database = schema_editor.connection.alias

    for organization in Organization.objects.using(database).order_by("pk").iterator():
        legacy_slug = slugify(organization.name) or "company"
        if organization.slug != legacy_slug:
            continue

        base_slug = slugify(organization.name.translate(POLISH_SLUG_TRANSLATION)) or "company"
        if base_slug == legacy_slug:
            continue

        candidate = base_slug[:255].rstrip("-") or "company"
        suffix = 2
        while Organization.objects.using(database).exclude(pk=organization.pk).filter(
            slug=candidate
        ).exists():
            suffix_text = f"-{suffix}"
            candidate = f"{base_slug[:255 - len(suffix_text)].rstrip('-')}{suffix_text}"
            suffix += 1

        organization.slug = candidate
        organization.save(using=database, update_fields=["slug"])


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0021_product_product_type"),
    ]

    operations = [
        migrations.RunPython(transliterate_generated_slugs, migrations.RunPython.noop),
    ]
