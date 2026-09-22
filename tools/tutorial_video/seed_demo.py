"""Populate the isolated tutorial database with a realistic Pro customer."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sentai.settings.tutorial_video")

import django

django.setup()

from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.billing.models import BillingPlanPrice, BillingSubscription
from apps.companies.models import ContentEntry, Organization, Product, SocialProfile, Tag


DEMO_PASSWORD = "Xoaila-demo-2026!"


def run():
    User = get_user_model()
    user, _ = User.objects.update_or_create(
        username="demo.customer",
        defaults={
            "email": "demo@xoaila.example",
            "company_name": "Greenwise Studio",
            "country": "PL",
            "preferred_language": "pl",
            "plan_tier": "PRO",
            "plan_selected_at": timezone.now(),
            "paid_plan_started_at": timezone.now(),
            "email_verified_at": timezone.now(),
            "registration_pending": False,
            "is_active": True,
        },
    )
    user.set_password(DEMO_PASSWORD)
    user.save()

    for tier, pln, eur in (("BASIC", 10000, 2500), ("PLUS", 20000, 4600), ("PRO", 40000, 9300)):
        for currency, amount in (("pln", pln), ("eur", eur)):
            BillingPlanPrice.objects.update_or_create(
                tier=tier,
                currency=currency,
                active_for_new_customers=True,
                defaults={
                    "stripe_price_id": f"price_tutorial_{tier.lower()}_{currency}",
                    "amount": amount,
                    "interval": "year",
                    "notes": "Tutorial data only",
                },
            )

    BillingSubscription.objects.update_or_create(
        user=user,
        defaults={
            "tier": "PRO",
            "status": "active",
            "stripe_customer_id": "cus_tutorial",
            "stripe_subscription_id": "sub_tutorial",
            "stripe_price_id": "price_tutorial_pro_pln",
            "current_period_start": timezone.now(),
            "current_period_end": timezone.now() + timezone.timedelta(days=365),
        },
    )

    organization, _ = Organization.objects.update_or_create(
        slug="greenwise-studio",
        defaults={
            "owner": user,
            "name": "Greenwise Studio",
            "company_type": "services",
            "website_url": "https://greenwise.example",
            "contact_email": "hello@greenwise.example",
            "phone_number": "+48 500 600 700",
            "address_line": "ul. Zielona 12",
            "city": "Warszawa",
            "postal_code": "00-001",
            "country": "Polska",
            "primary_language": "pl",
            "content_languages": ["pl", "en", "de"],
            "descriptions_by_language": {
                "pl": {
                    "short": "Projektujemy i pielęgnujemy ekologiczne ogrody dla firm i klientów indywidualnych.",
                    "long": "Greenwise Studio tworzy ogrody, tarasy i zielone przestrzenie w Warszawie i okolicach. Łączymy projektowanie, dobór roślin, wykonanie i całoroczną opiekę. Każdą ofertę przygotowujemy na podstawie wizji lokalnej i jawnego kosztorysu.",
                },
                "en": {
                    "short": "We design and maintain sustainable gardens for businesses and homeowners.",
                    "long": "Greenwise Studio creates gardens, terraces and green spaces in Warsaw and nearby areas. We combine design, planting, construction and year-round care. Every proposal follows a site visit and includes a transparent estimate.",
                },
                "de": {
                    "short": "Wir planen und pflegen nachhaltige Gärten für Unternehmen und Privatkunden.",
                    "long": "Greenwise Studio gestaltet Gärten, Terrassen und Grünflächen in Warschau und Umgebung. Unser Angebot umfasst Planung, Bepflanzung, Ausführung und ganzjährige Pflege.",
                },
            },
            "verification_status": "human_admin_verified",
            "verified_at": timezone.now(),
            "last_reviewed_at": timezone.now(),
            "public": True,
            "allow_ai_indexing": True,
        },
    )
    organization.tags.all().delete()
    for language, values in {
        "pl": ["projektowanie ogrodów", "zielone dachy", "pielęgnacja zieleni", "Warszawa"],
        "en": ["garden design", "green roofs", "garden maintenance", "Warsaw"],
        "de": ["Gartenplanung", "Dachbegrünung", "Gartenpflege", "Warschau"],
    }.items():
        Tag.objects.bulk_create([Tag(organization=organization, language=language, name=value) for value in values])

    organization.social_profiles.all().delete()
    for network, url in (
        ("linkedin", "https://www.linkedin.com/company/greenwise-studio"),
        ("instagram", "https://www.instagram.com/greenwise.studio"),
        ("youtube", "https://www.youtube.com/@greenwisestudio"),
    ):
        SocialProfile.objects.create(organization=organization, network=network, url=url)

    organization.products.all().delete()
    products = [
        ({"pl": "Projekt ogrodu", "en": "Garden design", "de": "Gartenplanung"},
         {"pl": "Kompletny projekt z doborem roślin i kosztorysem.", "en": "A complete design with planting plan and estimate.", "de": "Komplette Planung mit Pflanzplan und Kostenschätzung."},
         "https://greenwise.example/projekt-ogrodu"),
        ({"pl": "Pielęgnacja sezonowa", "en": "Seasonal maintenance", "de": "Saisonale Pflege"},
         {"pl": "Regularna opieka nad ogrodem w wybranym zakresie.", "en": "Scheduled garden care tailored to the property.", "de": "Regelmäßige, individuell abgestimmte Gartenpflege."},
         "https://greenwise.example/pielegnacja"),
        ({"pl": "Zielony taras", "en": "Green terrace", "de": "Grüne Terrasse"},
         {"pl": "Projekt i wykonanie zieleni na tarasie lub balkonie.", "en": "Design and installation of terrace or balcony greenery.", "de": "Planung und Umsetzung von Terrassen- oder Balkonbegrünung."},
         "https://greenwise.example/zielony-taras"),
    ]
    for names, descriptions, url in products:
        Product.objects.create(
            organization=organization,
            name=names["pl"],
            names_by_language=names,
            descriptions_by_language=descriptions,
            short_description_pl=descriptions["pl"],
            short_description_en=descriptions["en"],
            product_url=url,
        )

    organization.content_entries.all().delete()
    faqs = [
        (
            {"pl": "Na jakim obszarze działacie?", "en": "What area do you serve?", "de": "In welchem Gebiet sind Sie tätig?"},
            {"pl": "Obsługujemy Warszawę i miejscowości do 50 km od miasta.", "en": "We serve Warsaw and locations within 50 km of the city.", "de": "Wir arbeiten in Warschau und im Umkreis von 50 km."},
        ),
        (
            {"pl": "Czy pierwsza konsultacja jest płatna?", "en": "Is the first consultation paid?", "de": "Ist die Erstberatung kostenpflichtig?"},
            {"pl": "Krótka rozmowa telefoniczna jest bezpłatna. Wizję lokalną wyceniamy indywidualnie.", "en": "A short phone consultation is free. Site visits are priced individually.", "de": "Ein kurzes Telefongespräch ist kostenlos. Vor-Ort-Termine werden individuell berechnet."},
        ),
        (
            {"pl": "Ile trwa przygotowanie projektu?", "en": "How long does a design take?", "de": "Wie lange dauert eine Planung?"},
            {"pl": "Standardowy projekt przygotowujemy w ciągu 3–5 tygodni od wizji lokalnej.", "en": "A standard design takes 3–5 weeks after the site visit.", "de": "Eine Standardplanung dauert 3–5 Wochen nach dem Vor-Ort-Termin."},
        ),
    ]
    for index, (questions, answers) in enumerate(faqs):
        ContentEntry.objects.create(
            organization=organization,
            entry_type="faq",
            title=questions["pl"],
            questions_by_language=questions,
            answers_by_language=answers,
            summary_pl=answers["pl"],
            summary_en=answers["en"],
            content_url="https://greenwise.example/faq",
            is_featured=index == 0,
        )

    print(f"DEMO_USER={user.username}")
    print(f"DEMO_PASSWORD={DEMO_PASSWORD}")
    print(f"ORGANIZATION_ID={organization.pk}")


if __name__ == "__main__":
    run()
