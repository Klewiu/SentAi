from django.db import migrations, models


def split_product_translations(apps, schema_editor):
    Product = apps.get_model("companies", "Product")
    database = schema_editor.connection.alias
    products = list(
        Product.objects.using(database)
        .select_related("organization")
        .order_by("pk")
    )
    for product in products:
        organization = product.organization
        primary_language = organization.primary_language
        names = product.names_by_language or {}
        descriptions = product.descriptions_by_language or {}
        languages = list(dict.fromkeys(
            [primary_language]
            + list(organization.content_languages or [])
            + list(names)
            + list(descriptions)
        ))
        translations = []
        for language in languages:
            name = (names.get(language) or "").strip()
            if language == primary_language:
                name = name or product.name
            if not name:
                continue
            description = (
                descriptions.get(language)
                or getattr(product, f"short_description_{language}", "")
                or ""
            )
            translations.append((language, name, description))

        for index, (language, name, description) in enumerate(translations):
            if index == 0:
                localized_product = product
            else:
                localized_product = Product.objects.using(database).create(
                    organization_id=product.organization_id,
                    name=name,
                    product_url=product.product_url,
                    price_from=product.price_from,
                    currency=product.currency,
                    is_featured=product.is_featured,
                    created_at=product.created_at,
                )
            localized_product.language = language
            localized_product.name = name
            localized_product.names_by_language = {}
            localized_product.descriptions_by_language = {language: description} if description else {}
            localized_product.short_description_en = description if language == "en" else ""
            localized_product.short_description_pl = description if language == "pl" else ""
            localized_product.save(using=database)


class Migration(migrations.Migration):
    dependencies = [("companies", "0018_public_resource_ids")]

    operations = [
        migrations.AddField(
            model_name="product",
            name="language",
            field=models.CharField(blank=True, default="", max_length=2),
        ),
        migrations.RunPython(split_product_translations, migrations.RunPython.noop),
    ]
