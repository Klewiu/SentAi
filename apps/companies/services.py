from django.conf import settings
from django.urls import reverse


def absolute_url(route: str, request=None) -> str:
    if request is not None:
        return request.build_absolute_uri(route)
    return f"{settings.SITE_BASE_URL}{route}"


def compact(value):
    if isinstance(value, dict):
        return {
            key: compact(item)
            for key, item in value.items()
            if item not in (None, "", [], {})
        }
    if isinstance(value, list):
        return [compact(item) for item in value if item not in (None, "", [], {})]
    return value


def public_feed_urls(organization, request=None) -> dict:
    return {
        "company_json": absolute_url(
            reverse("companies_api:public-company-json", kwargs={"slug": organization.slug}),
            request,
        ),
        "company_jsonld": absolute_url(
            reverse("companies_api:public-company-jsonld", kwargs={"slug": organization.slug}),
            request,
        ),
        "llms_txt": absolute_url(
            reverse("companies_api:public-company-llms", kwargs={"slug": organization.slug}),
            request,
        ),
    }


def build_basic_feed(organization, request=None) -> dict:
    subscription = organization.get_subscription()
    return {
        "profile_type": "company-profile",
        "profile_version": "1.0",
        "company": {
            "name": organization.name,
            "slug": organization.slug,
            "company_type": organization.company_type,
            "website": organization.website_url,
            "email": organization.owner.email,
            "phone": organization.phone_number,
            "primary_language": organization.primary_language,
            "descriptions": {
                "en": organization.localized_text("short_description", "en"),
                "pl": organization.localized_text("short_description", "pl"),
            },
            "address": {
                "street": organization.address_line,
                "city": organization.city,
                "postal_code": organization.postal_code,
                "country": organization.country,
            },
        },
        "visibility": {
            "public": organization.public,
            "allow_ai_indexing": organization.allow_ai_indexing,
        },
        "available_formats": {
            "company_json": True,
            "company_jsonld": subscription.supports("advanced_formats"),
            "llms_txt": subscription.supports("llms_txt"),
        },
        "feed_urls": public_feed_urls(organization, request),
        "updated_at": organization.updated_at.isoformat(),
    }


def build_jsonld_feed(organization, request=None) -> dict:
    return compact(
        {
            "@context": "https://schema.org",
            "@type": "Organization",
            "name": organization.name,
            "url": organization.website_url,
            "email": organization.owner.email,
            "telephone": organization.phone_number,
            "description": organization.localized_text("long_description", "en")
            or organization.localized_text("short_description", "en"),
            "keywords": ", ".join(tag.name for tag in organization.tags.all()),
            "sameAs": [profile.url for profile in organization.social_profiles.all()],
            "inLanguage": ["en", "pl"],
            "address": {
                "@type": "PostalAddress",
                "streetAddress": organization.address_line,
                "addressLocality": organization.city,
                "postalCode": organization.postal_code,
                "addressCountry": organization.country,
            },
            "hasOfferCatalog": {
                "@type": "OfferCatalog",
                "name": f"{organization.name} products",
                "itemListElement": [
                    compact(
                        {
                            "@type": "Offer",
                            "name": product.name,
                            "description": product.localized_summary("en"),
                            "url": product.product_url,
                            "priceCurrency": product.currency if product.price_from else None,
                            "price": str(product.price_from) if product.price_from is not None else None,
                        }
                    )
                    for product in organization.products.all()
                ],
            },
            "subjectOf": [
                compact(
                    {
                        "@type": "CreativeWork",
                        "name": entry.title,
                        "url": entry.content_url,
                        "description": entry.localized_summary("en"),
                        "datePublished": entry.published_at.date().isoformat(),
                    }
                )
                for entry in organization.content_entries.all()
            ],
            "mainEntityOfPage": absolute_url(
                reverse("companies_api:public-company-json", kwargs={"slug": organization.slug}),
                request,
            ),
        }
    )


def build_llms_text(organization, request=None) -> str:
    sections = [
        f"# {organization.name}",
        "",
        organization.localized_text("long_description", "en")
        or organization.localized_text("short_description", "en"),
        "",
        "## Canonical feeds",
        f"- company.json: {public_feed_urls(organization, request)['company_json']}",
        f"- company.jsonld: {public_feed_urls(organization, request)['company_jsonld']}",
        "",
        "## Contact",
        f"- Website: {organization.website_url or 'n/a'}",
        f"- Email: {organization.owner.email or 'n/a'}",
        f"- Phone: {organization.phone_number or 'n/a'}",
        "",
        "## Topics",
    ]

    tags = list(organization.tags.values_list("name", flat=True))
    if tags:
        sections.extend(f"- {tag}" for tag in tags)
    else:
        sections.append("- No tags published")

    sections.extend(["", "## Products"]) 
    products = list(organization.products.all())
    if products:
        sections.extend(
            f"- {product.name}: {product.localized_summary('en') or 'No description'}"
            for product in products
        )
    else:
        sections.append("- No products published")

    sections.extend(["", "## Recent entries"])
    entries = list(organization.content_entries.all()[:10])
    if entries:
        sections.extend(
            f"- {entry.title}: {entry.localized_summary('en') or 'No summary'}"
            for entry in entries
        )
    else:
        sections.append("- No entries published")

    return "\n".join(sections).strip() + "\n"
