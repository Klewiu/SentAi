from django.conf import settings
from django.urls import reverse


SUPPORTED_DESCRIPTION_LANGUAGES = tuple(
    code for code, _label in getattr(settings, "FEED_LANGUAGES", settings.LANGUAGES)
)


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


def _ordered_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _description_payload(organization) -> dict:
    stored_descriptions = organization.descriptions_by_language or {}
    preferred_languages = _ordered_unique(
        list(organization.content_languages or [])
        + [organization.primary_language]
        + list(SUPPORTED_DESCRIPTION_LANGUAGES)
    )
    descriptions = {}
    for language_code in preferred_languages:
        short_field = f"short_description_{language_code}"
        long_field = f"long_description_{language_code}"
        short_value = (stored_descriptions.get(language_code, {}).get("short") or "").strip()
        long_value = (stored_descriptions.get(language_code, {}).get("long") or "").strip()

        # Legacy fallback for historical records still using fixed EN/PL columns.
        if not short_value and hasattr(organization, short_field):
            short_value = getattr(organization, short_field, "")
        if not long_value and hasattr(organization, long_field):
            long_value = getattr(organization, long_field, "")

        if short_value or long_value:
            descriptions[language_code] = compact(
                {
                    "short": short_value,
                    "long": long_value,
                }
            )
    return descriptions


def _product_description_payload(product) -> dict:
    descriptions = {}
    for language_code in SUPPORTED_DESCRIPTION_LANGUAGES:
        field_name = f"short_description_{language_code}"
        if not hasattr(product, field_name):
            continue
        value = getattr(product, field_name, "")
        if value:
            descriptions[language_code] = value
    return descriptions


def _entry_summary_payload(entry) -> dict:
    summaries = {}
    for language_code in SUPPORTED_DESCRIPTION_LANGUAGES:
        field_name = f"summary_{language_code}"
        if not hasattr(entry, field_name):
            continue
        value = getattr(entry, field_name, "")
        if value:
            summaries[language_code] = value
    return summaries


def _social_profiles_payload(organization) -> list[dict]:
    return [
        {
            "network": profile.network,
            "label": profile.get_network_display(),
            "url": profile.url,
        }
        for profile in organization.social_profiles.all()
    ]


def _tags_payload(organization) -> list[dict]:
    return [
        compact(
            {
                "name": tag.name,
                "language": tag.language,
            }
        )
        for tag in organization.tags.all()
    ]


def _products_payload(organization) -> list[dict]:
    products_by_language = organization.products_by_language or {}
    if products_by_language:
        merged_products: dict[str, dict] = {}
        sequence = 0
        for language_code, items in products_by_language.items():
            for item in items:
                name = (item.get("name") or "").strip()
                description = (item.get("description") or "").strip()
                product_url = (item.get("url") or "").strip()
                if not name:
                    continue

                key = product_url or f"name:{name.lower()}:{sequence}"
                if key not in merged_products:
                    merged_products[key] = {
                        "name": name,
                        "descriptions": {},
                        "product_url": product_url,
                        "is_featured": sequence == 0,
                    }
                if description:
                    merged_products[key]["descriptions"][language_code] = description
                if product_url and not merged_products[key].get("product_url"):
                    merged_products[key]["product_url"] = product_url
                sequence += 1

        return [compact(payload) for payload in merged_products.values()]

    return [
        compact(
            {
                "name": product.name,
                "descriptions": _product_description_payload(product),
                "product_url": product.product_url,
                "price_from": str(product.price_from) if product.price_from is not None else None,
                "currency": product.currency if product.price_from is not None else None,
                "is_featured": product.is_featured,
                "created_at": product.created_at.isoformat(),
            }
        )
        for product in organization.products.all()
    ]


def _content_entries_payload(organization) -> list[dict]:
    return [
        compact(
            {
                "entry_type": entry.entry_type,
                "entry_type_label": entry.get_entry_type_display(),
                "title": entry.title,
                "summaries": _entry_summary_payload(entry),
                "content_url": entry.content_url,
                "published_at": entry.published_at.isoformat(),
                "is_featured": entry.is_featured,
            }
        )
        for entry in organization.content_entries.all()
    ]


def _company_keywords(organization) -> list[str]:
    values = [organization.name, organization.get_company_type_display()]
    values.extend(tag.name for tag in organization.tags.all())
    return _ordered_unique([value.strip() for value in values if value and value.strip()])


def build_basic_feed(organization, request=None) -> dict:
    subscription = organization.get_subscription()
    descriptions = _description_payload(organization)
    return compact(
        {
            "profile_type": "company-profile",
            "profile_version": "2.0",
            "company": {
                "name": organization.name,
                "slug": organization.slug,
                "company_type": organization.company_type,
                "company_type_label": organization.get_company_type_display(),
                "website": organization.website_url,
                "contact": {
                    "email": organization.owner.email,
                    "phone": organization.phone_number,
                    "address": {
                        "street": organization.address_line,
                        "city": organization.city,
                        "postal_code": organization.postal_code,
                        "country": organization.country,
                    },
                },
                "languages": {
                    "primary": organization.primary_language,
                    "declared_content_languages": organization.content_languages,
                    "available_description_languages": list(descriptions.keys()),
                },
                "descriptions": descriptions,
                "ai_summary": organization.ai_summary,
            },
            "discovery": {
                "keywords": _company_keywords(organization),
                "social_profiles": _social_profiles_payload(organization),
                "tags": _tags_payload(organization),
                "products": _products_payload(organization),
                "content_entries": _content_entries_payload(organization),
            },
            "visibility": {
                "public": organization.public,
                "allow_ai_indexing": organization.allow_ai_indexing,
            },
            "provenance": {
                "source_type": organization.source_type,
                "source_url": organization.source_url,
                "verification_status": organization.verification_status,
                "verified_at": organization.verified_at.isoformat() if organization.verified_at else None,
            },
            "ai_access": {
                "subscription_tier": subscription.tier,
                "available_formats": {
                    "company_json": True,
                    "company_jsonld": subscription.supports("advanced_formats"),
                    "llms_txt": subscription.supports("llms_txt"),
                },
                "feed_urls": public_feed_urls(organization, request),
            },
            "timestamps": {
                "created_at": organization.created_at.isoformat(),
                "updated_at": organization.updated_at.isoformat(),
            },
        }
    )


def build_jsonld_feed(organization, request=None) -> dict:
    description_map = _description_payload(organization)
    keywords = _company_keywords(organization)
    available_languages = list(description_map.keys()) or _ordered_unique(
        list(organization.content_languages or []) + [organization.primary_language]
    )
    return compact(
        {
            "@context": "https://schema.org",
            "@type": "Organization",
            "@id": absolute_url(
                reverse("companies_api:public-company-json", kwargs={"slug": organization.slug}),
                request,
            ),
            "name": organization.name,
            "identifier": organization.slug,
            "url": organization.website_url,
            "email": organization.owner.email,
            "telephone": organization.phone_number,
            "description": organization.localized_text("long_description", organization.primary_language)
            or organization.localized_text("short_description", organization.primary_language),
            "keywords": ", ".join(keywords),
            "sameAs": [profile.url for profile in organization.social_profiles.all()],
            "inLanguage": available_languages,
            "availableLanguage": available_languages,
            "knowsAbout": [tag.name for tag in organization.tags.all()],
            "contactPoint": [
                compact(
                    {
                        "@type": "ContactPoint",
                        "email": organization.owner.email,
                        "telephone": organization.phone_number,
                        "availableLanguage": available_languages,
                        "contactType": "customer support",
                    }
                )
            ],
            "address": {
                "@type": "PostalAddress",
                "streetAddress": organization.address_line,
                "addressLocality": organization.city,
                "postalCode": organization.postal_code,
                "addressCountry": organization.country,
            },
            "areaServed": organization.country,
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
            "mainEntityOfPage": organization.website_url
            or absolute_url(
                reverse("companies_api:public-company-json", kwargs={"slug": organization.slug}),
                request,
            ),
        }
    )


def build_llms_text(organization, request=None) -> str:
    descriptions = _description_payload(organization)
    keywords = _company_keywords(organization)
    feed_urls = public_feed_urls(organization, request)
    sections = [
        f"# {organization.name}",
        "",
        organization.localized_text("long_description", organization.primary_language)
        or organization.localized_text("short_description", organization.primary_language),
        "",
        "## Company facts",
        f"- Brand name: {organization.name}",
        f"- Company type: {organization.get_company_type_display()}",
        f"- Primary language: {organization.primary_language or 'n/a'}",
        f"- Declared content languages: {', '.join(organization.content_languages or []) or 'n/a'}",
        "",
        "## Canonical feeds",
        f"- company.json: {feed_urls['company_json']}",
        f"- company.jsonld: {feed_urls['company_jsonld']}",
        f"- llms.txt: {feed_urls['llms_txt']}",
        "",
        "## Contact",
        f"- Website: {organization.website_url or 'n/a'}",
        f"- Email: {organization.owner.email or 'n/a'}",
        f"- Phone: {organization.phone_number or 'n/a'}",
        f"- Address: {', '.join(value for value in [organization.address_line, organization.postal_code, organization.city, organization.country] if value) or 'n/a'}",
        "",
        "## Topics",
    ]

    if keywords:
        sections.extend(f"- {keyword}" for keyword in keywords)
    else:
        sections.append("- No topics published")

    if descriptions:
        sections.extend(["", "## Descriptions by language"])
        for language_code, values in descriptions.items():
            sections.append(f"### {language_code}")
            if values.get("short"):
                sections.append(f"- Short: {values['short']}")
            if values.get("long"):
                sections.append(f"- Long: {values['long']}")

    social_profiles = _social_profiles_payload(organization)
    sections.extend(["", "## Social profiles"])
    if social_profiles:
        sections.extend(
            f"- {profile['label']}: {profile['url']}"
            for profile in social_profiles
        )
    else:
        sections.append("- No social profiles published")

    sections.extend(["", "## Products"]) 
    products = _products_payload(organization)
    if products:
        sections.extend(
            f"- {product['name']}: {next(iter(product.get('descriptions', {}).values()), 'No description')}"
            for product in products
        )
    else:
        sections.append("- No products published")

    sections.extend(["", "## Recent entries"])
    entries = _content_entries_payload(organization)[:10]
    if entries:
        sections.extend(
            f"- {entry['title']}: {next(iter(entry.get('summaries', {}).values()), 'No summary')}"
            for entry in entries
        )
    else:
        sections.append("- No entries published")

    return "\n".join(sections).strip() + "\n"
