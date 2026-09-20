from django.db import migrations


def migrate_translations(apps, schema_editor):
    Organization = apps.get_model("companies", "Organization")
    Product = apps.get_model("companies", "Product")
    for org in Organization.objects.using(schema_editor.connection.alias).iterator(chunk_size=100):
        products = list(Product.objects.using(schema_editor.connection.alias).filter(organization=org).order_by("-is_featured", "name"))
        for language, rows in (org.products_by_language or {}).items():
            for index, row in enumerate(rows):
                name = (row.get("name") or "").strip()
                if not name:
                    continue
                url = (row.get("url") or "").strip()
                product = next((p for p in products if (url and p.product_url == url) or p.name == name), None)
                if product is None and index < len(products):
                    product = products[index]
                if product is None:
                    product = Product.objects.using(schema_editor.connection.alias).create(organization=org, name=name, product_url=url)
                    products.append(product)
                product.descriptions_by_language = {**(product.descriptions_by_language or {}), language: row.get("description", "")}
                product.save(using=schema_editor.connection.alias, update_fields=["descriptions_by_language"])


class Migration(migrations.Migration):
    dependencies = [("companies", "0014_product_descriptions_by_language")]
    operations = [migrations.RunPython(migrate_translations, migrations.RunPython.noop)]
