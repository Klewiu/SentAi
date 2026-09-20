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


def public_profile_url(organization, language_code: str, request=None) -> str:
    route = reverse(
        "public-company-language-detail",
        kwargs={"slug": organization.slug, "content_lang": language_code},
    )
    return absolute_url(route, request)


def public_feed_urls(organization, request=None, language_code: str | None = None) -> dict:
    urls = {
        "company_json": absolute_url(
            reverse("companies_api:public-company-json", kwargs={"slug": organization.slug}),
            request,
        ),
        "company_jsonld": absolute_url(
            reverse("companies_api:public-company-jsonld", kwargs={"slug": organization.slug}),
            request,
        ),
        "company_md": absolute_url(
            reverse("companies_api:public-company-md", kwargs={"slug": organization.slug}),
            request,
        ),
        "llms_txt": absolute_url(
            reverse("companies_api:public-company-llms", kwargs={"slug": organization.slug}),
            request,
        ),
    }
    if language_code:
        urls["multilingual_company_json"] = urls["company_json"]
        urls.update({
            "company_json": absolute_url(reverse(
                "companies_api:public-company-language-json",
                kwargs={"slug": organization.slug, "language_code": language_code},
            ), request),
            "company_jsonld": absolute_url(reverse(
                "companies_api:public-company-language-jsonld",
                kwargs={"slug": organization.slug, "language_code": language_code},
            ), request),
            "company_md": absolute_url(reverse(
                "companies_api:public-company-language-md",
                kwargs={"slug": organization.slug, "language_code": language_code},
            ), request),
            "llms_txt": absolute_url(reverse(
                "companies_api:public-company-language-llms",
                kwargs={"slug": organization.slug, "language_code": language_code},
            ), request),
        })
    return urls


def _ordered_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _description_payload(organization) -> dict:
    stored_descriptions = organization.descriptions_by_language or {}
    preferred_languages = _ordered_unique(
        [organization.primary_language]
        + list(organization.content_languages or [])
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
    limit = organization.get_subscription().limit_for("languages")
    return dict(list(descriptions.items())[:limit])


def profile_language_choices(organization):
    """Only offer supplied translations available under the effective plan."""
    labels = dict(getattr(settings, "FEED_LANGUAGES", settings.LANGUAGES))
    codes = list(_description_payload(organization)) or [organization.primary_language]
    return [{"code": code, "label": labels.get(code, code.upper())} for code in codes]


def primary_public_language(organization) -> str:
    codes = [item["code"] for item in profile_language_choices(organization)]
    if organization.primary_language in codes:
        return organization.primary_language
    return codes[0]


def _product_description_payload(product) -> dict:
    allowed_languages = set(_description_payload(product.organization))
    descriptions = {
        code: value for code, value in (product.descriptions_by_language or {}).items()
        if code in allowed_languages
    }
    for language_code in SUPPORTED_DESCRIPTION_LANGUAGES:
        if language_code not in allowed_languages:
            continue
        field_name = f"short_description_{language_code}"
        if not hasattr(product, field_name):
            continue
        value = getattr(product, field_name, "")
        if value:
            descriptions[language_code] = value
    return descriptions


def _entry_summary_payload(entry) -> dict:
    allowed_languages = set(_description_payload(entry.organization))
    summaries = {
        code: value for code, value in (entry.answers_by_language or {}).items()
        if code in allowed_languages
    }
    for language_code in SUPPORTED_DESCRIPTION_LANGUAGES:
        if language_code not in allowed_languages:
            continue
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
        for profile in public_resources(organization, "social_profiles")
    ]


def _tags_payload(organization, language_code: str | None = None) -> list[dict]:
    return [
        compact(
            {
                "name": tag.name,
                "language": tag.language,
            }
        )
        for tag in public_resources(organization, "tags")
        if not language_code or not tag.language or tag.language == language_code
    ]


def _products_payload(organization) -> list[dict]:
    languages = [item["code"] for item in profile_language_choices(organization)]
    payload = []
    for product in public_resources(organization, "products"):
        descriptions = _product_description_payload(product)
        translations = {}
        for code in languages:
            translation = product.translation_in(code)
            if translation:
                translations[code] = compact(translation)
        payload.append(compact({
                "id": str(product.public_id),
                "name": product.localized_name(organization.primary_language),
                "names_by_language": {
                    code: values["name"] for code, values in translations.items()
                },
                "descriptions": descriptions,
                "translations": translations,
                "product_url": product.product_url,
                "price_from": str(product.price_from) if product.price_from is not None else None,
                "currency": product.currency if product.price_from is not None else None,
                "is_featured": product.is_featured,
                "created_at": product.created_at.isoformat(),
            }))
    return payload


def _content_entries_payload(organization) -> list[dict]:
    languages = [item["code"] for item in profile_language_choices(organization)]
    payload = []
    for entry in public_resources(organization, "content_entries"):
        summaries = _entry_summary_payload(entry)
        translations = {}
        for code in languages:
            translation = entry.translation_in(code)
            if translation:
                translations[code] = compact(translation)
        payload.append(compact({
                "id": str(entry.public_id),
                "entry_type": entry.entry_type,
                "entry_type_label": entry.get_entry_type_display(),
                "title": entry.localized_question(organization.primary_language),
                "questions": {
                    code: value for code, value in (entry.questions_by_language or {}).items()
                    if code in languages
                },
                "summaries": summaries,
                "translations": translations,
                "content_url": entry.content_url,
                "published_at": entry.published_at.isoformat(),
                "is_featured": entry.is_featured,
            }))
    return payload


def _company_keywords(organization, language_code: str | None = None) -> list[str]:
    values = [organization.name, organization.get_company_type_display()]
    values.extend(tag["name"] for tag in _tags_payload(organization, language_code))
    return _ordered_unique([value.strip() for value in values if value and value.strip()])


def build_basic_feed(organization, request=None) -> dict:
    subscription = organization.get_subscription()
    descriptions = _description_payload(organization)
    return compact(
        {
            "profile_type": "company-profile",
            "profile_version": "2.0",
            "company": {
                "id": str(organization.public_id),
                "name": organization.name,
                "slug": organization.slug,
                "company_type": organization.company_type,
                "company_type_label": organization.get_company_type_display(),
                "website": organization.website_url,
                "contact": {
                    "email": organization.contact_email,
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
                    "declared_content_languages": list(descriptions),
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
                "last_reviewed_at": organization.last_reviewed_at.isoformat() if organization.last_reviewed_at else None,
            },
            "ai_access": {
                "subscription_tier": subscription.tier,
                "available_formats": {
                    "company_json": True,
                    "company_jsonld": subscription.supports("advanced_formats"),
                    "company_md": subscription.supports("company_md"),
                    "llms_txt": subscription.supports("llms_txt"),
                },
                "feed_urls": public_feed_urls(organization, request),
                "profile_urls": {
                    item["code"]: public_profile_url(organization, item["code"], request)
                    for item in profile_language_choices(organization)
                },
                "localized_feed_urls": {
                    item["code"]: public_feed_urls(organization, request, item["code"])
                    for item in profile_language_choices(organization)
                },
            },
            "timestamps": {
                "created_at": organization.created_at.isoformat(),
                "updated_at": organization.updated_at.isoformat(),
            },
        }
    )


def build_localized_feed(organization, language_code: str, request=None) -> dict:
    descriptions = _description_payload(organization)
    description = descriptions.get(language_code, {})
    payload = build_basic_feed(organization, request)
    payload["company"]["languages"] = {
        "primary": language_code,
        "available": list(descriptions),
    }
    payload["company"]["descriptions"] = {language_code: description}
    # This field has no translation map, so it belongs only in the multilingual master feed.
    payload["company"].pop("ai_summary", None)
    payload["discovery"]["keywords"] = _company_keywords(organization, language_code)
    payload["discovery"]["tags"] = _tags_payload(organization, language_code)
    localized_products = []
    for product in payload["discovery"].get("products", []):
        translated = product.get("translations", {}).get(language_code, {})
        if not translated.get("name"):
            continue
        product["name"] = translated["name"]
        product["descriptions"] = {language_code: translated.get("description")} if translated.get("description") else {}
        product["translations"] = {language_code: translated} if translated else {}
        product["names_by_language"] = {language_code: translated.get("name")} if translated.get("name") else {}
        localized_products.append(product)
    payload["discovery"]["products"] = localized_products
    localized_entries = []
    for entry in payload["discovery"].get("content_entries", []):
        translated = entry.get("translations", {}).get(language_code, {})
        if not translated.get("question"):
            continue
        entry["title"] = translated["question"]
        entry["questions"] = {language_code: translated.get("question")} if translated.get("question") else {}
        entry["summaries"] = {language_code: translated.get("answer")} if translated.get("answer") else {}
        entry["translations"] = {language_code: translated} if translated else {}
        localized_entries.append(entry)
    payload["discovery"]["content_entries"] = localized_entries
    payload["ai_access"]["feed_urls"] = public_feed_urls(organization, request, language_code)
    payload["canonical_profile"] = public_profile_url(organization, language_code, request)
    return compact(payload)


def build_jsonld_feed(organization, request=None, language_code=None) -> dict:
    language_code = language_code or primary_public_language(organization)
    description_map = _description_payload(organization)
    keywords = _company_keywords(organization, language_code)
    available_languages = list(description_map.keys()) or _ordered_unique(
        list(organization.content_languages or []) + [organization.primary_language]
    )
    canonical_page = public_profile_url(organization, language_code, request)
    return compact(
        {
            "@context": "https://schema.org",
            "@type": "Organization",
            "@id": f"urn:uuid:{organization.public_id}",
            "name": organization.name,
            "identifier": str(organization.public_id),
            "url": organization.website_url,
            "email": organization.contact_email,
            "telephone": organization.phone_number,
            "description": organization.localized_text("long_description", language_code)
            or organization.localized_text("short_description", language_code),
            "keywords": ", ".join(keywords),
            "sameAs": [profile.url for profile in public_resources(organization, "social_profiles")],
            "inLanguage": language_code,
            "availableLanguage": available_languages,
            "knowsAbout": [tag["name"] for tag in _tags_payload(organization, language_code)],
            "contactPoint": [
                compact(
                    {
                        "@type": "ContactPoint",
                        "email": organization.contact_email,
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
                            "name": product.localized_name(language_code),
                            "description": product.localized_summary(language_code),
                            "url": product.product_url,
                            "priceCurrency": product.currency if product.price_from else None,
                            "price": str(product.price_from) if product.price_from is not None else None,
                        }
                    )
                    for product in public_resources(organization, "products")
                    if product.translation_in(language_code)
                ],
            },
            "subjectOf": [
                compact({
                    "@type": "Question" if entry.entry_type == "faq" else "CreativeWork",
                    "@id": f"urn:uuid:{entry.public_id}",
                    "name": entry.localized_question(language_code),
                    "url": entry.content_url,
                    "acceptedAnswer": {
                        "@type": "Answer",
                        "text": entry.localized_answer(language_code),
                    } if entry.entry_type == "faq" else None,
                    "description": entry.localized_summary(language_code) if entry.entry_type != "faq" else None,
                    "datePublished": entry.published_at.date().isoformat(),
                    "inLanguage": language_code,
                })
                for entry in public_resources(organization, "content_entries")
                if entry.translation_in(language_code)
            ],
            "mainEntityOfPage": canonical_page,
        }
    )


def build_llms_text(organization, request=None, language_code: str | None = None) -> str:
    selected_language = language_code or primary_public_language(organization)
    descriptions = _description_payload(organization)
    keywords = _company_keywords(organization, selected_language if language_code else None)
    feed_urls = public_feed_urls(organization, request, language_code)
    sections = [
        f"# {organization.name}",
        "",
        organization.localized_text("long_description", selected_language)
        or organization.localized_text("short_description", selected_language),
        "",
        "## Company facts",
        f"- Brand name: {organization.name}",
        f"- Company type: {organization.get_company_type_display()}",
        f"- Content language: {selected_language}",
        f"- Declared content languages: {', '.join(organization.content_languages or []) or 'n/a'}",
        "",
        "## Canonical feeds",
        f"- company.json: {feed_urls['company_json']}",
        f"- company.jsonld: {feed_urls['company_jsonld']}",
        f"- llms.txt: {feed_urls['llms_txt']}",
        f"- Last reviewed: {organization.last_reviewed_at.date().isoformat() if organization.last_reviewed_at else 'n/a'}",
        "",
        "## Contact",
        f"- Website: {organization.website_url or 'n/a'}",
        f"- Email: {organization.contact_email or 'n/a'}",
        f"- Phone: {organization.phone_number or 'n/a'}",
        f"- Address: {', '.join(value for value in [organization.address_line, organization.postal_code, organization.city, organization.country] if value) or 'n/a'}",
        "",
        "## Topics",
    ]

    if keywords:
        sections.extend(f"- {keyword}" for keyword in keywords)
    else:
        sections.append("- No topics published")

    if descriptions and not language_code:
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
    products = [
        product for product in _products_payload(organization)
        if not language_code or product.get("translations", {}).get(selected_language, {}).get("name")
    ]
    if products:
        for product in products:
            translated = product.get("translations", {}).get(selected_language, {})
            sections.append(
                f"- {translated.get('name') or product['name']}: "
                f"{translated.get('description') or 'No description'}"
            )
    else:
        sections.append("- No products published")

    sections.extend(["", "## Recent entries"])
    entries = [
        entry for entry in public_resources(organization, "content_entries")
        if not language_code or entry.translation_in(selected_language)
    ]
    if entries:
        sections.extend(
            f"- {entry.localized_question(selected_language)}: {entry.localized_summary(selected_language) or 'No summary'}"
            for entry in entries
        )
    else:
        sections.append("- No entries published")

    return "\n".join(sections).strip() + "\n"


def build_markdown_feed(organization, request=None, language_code: str | None = None) -> str:
    """Render a company profile as Markdown — optimised for LLM ingestion and RAG pipelines."""
    descriptions = _description_payload(organization)
    selected_language = language_code or primary_public_language(organization)
    feed_urls = public_feed_urls(organization, request, language_code)

    lines = [f"# {organization.name}", ""]

    if organization.ai_summary and not language_code:
        lines += [f"> {organization.ai_summary}", ""]

    description = (
        organization.localized_text("long_description", selected_language)
        or organization.localized_text("short_description", selected_language)
    )
    if description:
        lines += ["## About", "", description, ""]

    lines += ["## Company information", ""]
    lines.append(f"- **Type:** {organization.get_company_type_display()}")
    if organization.website_url:
        lines.append(f"- **Website:** {organization.website_url}")
    location = ", ".join(v for v in [organization.city, organization.country] if v)
    if location:
        lines.append(f"- **Location:** {location}")
    if organization.contact_email:
        lines.append(f"- **Email:** {organization.contact_email}")
    if organization.phone_number:
        lines.append(f"- **Phone:** {organization.phone_number}")
    lines.append(f"- **Content language:** {selected_language}")
    lines.append(f"- **Verification:** {organization.verification_status}")
    if organization.last_reviewed_at:
        lines.append(f"- **Last reviewed:** {organization.last_reviewed_at.date().isoformat()}")
    lines.append(f"- **Last updated:** {organization.updated_at.date().isoformat()}")
    lines.append("")

    tags = _tags_payload(organization, selected_language if language_code else None)
    if tags:
        lines += ["## Specializations", ""]
        lines.extend(f"- {tag['name']}" for tag in tags)
        lines.append("")

    products = [
        product for product in _products_payload(organization)
        if not language_code or product.get("translations", {}).get(selected_language, {}).get("name")
    ]
    if products:
        lines += ["## Products & services", ""]
        for product in products:
            translated = product.get("translations", {}).get(selected_language, {})
            name = translated.get("name") or product.get("name", "")
            desc = translated.get("description", "")
            price = product.get("price_from")
            currency = product.get("currency", "")
            url = product.get("product_url", "")
            lines.append(f"### {name}")
            if desc:
                lines.append(desc)
            details = []
            if price:
                details.append(f"From {price} {currency}".strip())
            if url:
                details.append(f"[More info]({url})")
            if details:
                lines.append(" · ".join(details))
            lines.append("")

    entries = [
        entry for entry in public_resources(organization, "content_entries")
        if not language_code or entry.translation_in(selected_language)
    ]
    if entries:
        lines += ["## Content & updates", ""]
        for entry in entries:
            summary = entry.localized_summary(selected_language)
            lines.append(f"### {entry.localized_question(selected_language)} _{entry.get_entry_type_display()}_")
            if summary:
                lines.append(summary)
            if entry.content_url:
                lines.append(f"[Read more]({entry.content_url})")
            lines.append("")

    social = _social_profiles_payload(organization)
    if social:
        lines += ["## Social profiles", ""]
        lines.extend(f"- [{p['label']}]({p['url']})" for p in social)
        lines.append("")

    if len(descriptions) > 1 and not language_code:
        lines += ["## Descriptions by language", ""]
        for lang, values in descriptions.items():
            lines.append(f"### {lang.upper()}")
            if values.get("short"):
                lines.append(f"**Short:** {values['short']}")
            if values.get("long"):
                lines.append(f"**Long:** {values['long']}")
            lines.append("")

    lines += [
        "## Machine-readable formats",
        "",
        f"- **JSON:** {feed_urls['company_json']}",
        f"- **JSON-LD:** {feed_urls['company_jsonld']}",
        f"- **Markdown:** {feed_urls['company_md']}",
        f"- **LLMs.txt:** {feed_urls['llms_txt']}",
        "",
    ]

    return "\n".join(lines).strip() + "\n"


def public_resources(organization, relation):
    limit = organization.get_subscription().limit_for(relation)
    return list(getattr(organization, relation).all())[:limit] if limit else []
