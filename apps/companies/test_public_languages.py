import json
import re
from datetime import timedelta

from django.test import TestCase
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import override

from apps.accounts.models import User
from apps.billing.models import BillingSubscription

from .models import ContentEntry, Organization, Product, Tag, VerificationStatus


class PublicLanguageTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="languages", email="languages@example.com", plan_tier="PRO")
        self.org = Organization.objects.create(
            owner=user,
            name="Languages",
            primary_language="en",
            content_languages=["en", "pl"],
            descriptions_by_language={
                "en": {"long": "English company description"},
                "pl": {"short": "Polski opis firmy"},
            },
        )
        self.product = Product.objects.create(
            organization=self.org,
            name="Product",
            names_by_language={"en": "English product", "pl": "Polski produkt"},
            descriptions_by_language={
                "en": "English service description",
                "pl": "Polski opis usługi",
            },
        )
        Organization.objects.filter(pk=self.org.pk).update(
            verification_status=VerificationStatus.HUMAN_ADMIN_VERIFIED
        )
        self.billing_subscription = BillingSubscription.objects.create(
            user=user,
            tier="PRO",
            status="active",
            current_period_end=timezone.now() + timedelta(days=365),
        )

    def page(self, interface_language, content_language=None):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = interface_language
        with override(interface_language):
            route = (
                reverse("public-company-language-detail", args=[self.org.slug, content_language])
                if content_language
                else reverse("public-company-detail", args=[self.org.slug])
            )
            return self.client.get(route, HTTP_ACCEPT_LANGUAGE=interface_language)

    def test_polish_page_uses_only_polish_company_content(self):
        response = self.page("pl", "pl")
        self.assertEqual(response.context["description"], "Polski opis firmy")
        self.assertContains(response, "Informacje o firmie")
        self.assertContains(response, "Polski produkt")
        self.assertNotContains(response, "English service description")
        self.assertEqual(response["Content-Language"], "pl")
        payload = json.loads(re.search(
            r'<script type="application/ld\+json">(.*?)</script>',
            response.content.decode(),
            re.S,
        ).group(1))
        self.assertEqual(payload["description"], "Polski opis firmy")
        self.assertEqual(payload["inLanguage"], "pl")

    def test_english_page_uses_english(self):
        response = self.page("en")
        self.assertEqual(response.context["description"], "English company description")
        self.assertContains(response, "Company facts")
        self.assertContains(response, "English product")

    def test_legacy_primary_language_without_content_uses_published_language(self):
        Organization.objects.filter(pk=self.org.pk).update(
            descriptions_by_language={"pl": {"long": "Polski opis"}},
            content_languages=["pl"],
            primary_language="en",
        )
        response = self.page("en")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["content_language"], "pl")
        self.assertEqual(response.context["description"], "Polski opis")

    def test_interface_language_does_not_select_company_translation(self):
        response = self.page("pl")
        self.assertEqual(response.context["content_language"], "en")
        self.assertEqual(response.context["description"], "English company description")
        self.assertContains(response, "Informacje o firmie")

        response = self.page("en", "pl")
        self.assertEqual(response.context["description"], "Polski opis firmy")
        self.assertContains(response, "Company facts")
        self.assertContains(response, f"/companies/{self.org.slug}/pl/")

    def test_unknown_or_archived_language_returns_not_found(self):
        self.assertEqual(self.page("en", "xx").status_code, 404)
        self.org.owner.plan_tier = "BASIC"
        self.org.owner.save(update_fields=["plan_tier"])
        self.billing_subscription.tier = "BASIC"
        self.billing_subscription.save(update_fields=["tier", "updated_at"])
        self.assertEqual(self.page("en", "pl").status_code, 404)

    def test_dashboard_lists_each_profile_language(self):
        self.client.force_login(self.org.owner)
        second = Organization.objects.create(
            owner=self.org.owner,
            name="Second profile",
            primary_language="pl",
            descriptions_by_language={"pl": {"long": "Second description"}},
        )
        with override("pl"):
            response = self.client.get(reverse("dashboard:home"))
        self.assertContains(response, self.org.name)
        self.assertContains(response, second.name)
        self.assertContains(response, f"/companies/{self.org.slug}/en/")
        self.assertContains(response, f"/companies/{self.org.slug}/pl/")
        self.assertNotContains(response, f"/companies/{second.slug}/pl/")

    def test_language_choices_follow_plan_limits(self):
        from .services import profile_language_choices

        self.org.content_languages = ["en", "pl", "de"]
        self.org.descriptions_by_language["de"] = {"long": "Deutsche Beschreibung"}
        self.org.save()
        for tier, count in [("BASIC", 1), ("PLUS", 2), ("PRO", 3)]:
            with self.subTest(tier=tier):
                self.org.owner.plan_tier = tier
                self.org.owner.save(update_fields=["plan_tier"])
                self.billing_subscription.tier = tier
                self.billing_subscription.save(update_fields=["tier", "updated_at"])
                org = Organization.objects.select_related("owner").get(pk=self.org.pk)
                self.assertEqual(len(profile_language_choices(org)), count)

    def test_localized_feeds_do_not_mix_product_faq_or_tag_languages(self):
        entry = ContentEntry.objects.create(
            organization=self.org,
            entry_type="faq",
            title="English fallback question",
            questions_by_language={"en": "English question?", "pl": "Polskie pytanie?"},
            answers_by_language={"en": "English answer.", "pl": "Polska odpowiedź."},
        )
        Tag.objects.create(organization=self.org, name="English topic", language="en")
        Tag.objects.create(organization=self.org, name="Polski temat", language="pl")
        response = self.client.get(
            reverse("companies_api:public-company-language-json", args=[self.org.slug, "pl"])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Language"], "pl")
        payload = response.json()
        self.assertEqual(payload["company"]["descriptions"], {"pl": {"short": "Polski opis firmy"}})
        self.assertEqual(payload["discovery"]["products"][0]["name"], "Polski produkt")
        self.assertEqual(payload["discovery"]["content_entries"][0]["title"], "Polskie pytanie?")
        self.assertEqual(payload["discovery"]["content_entries"][0]["id"], str(entry.public_id))
        self.assertEqual([tag["name"] for tag in payload["discovery"]["tags"]], ["Polski temat"])
        self.assertNotIn("English answer", response.content.decode())
        for route_name in (
            "public-company-language-jsonld",
            "public-company-language-md",
            "public-company-language-llms",
        ):
            with self.subTest(route=route_name):
                localized = self.client.get(
                    reverse(f"companies_api:{route_name}", args=[self.org.slug, "pl"])
                )
                self.assertEqual(localized.status_code, 200)
                self.assertEqual(localized["Content-Language"], "pl")
                self.assertIn('hreflang="x-default"', localized["Link"])
                self.assertNotIn("English answer", localized.content.decode())

    def test_master_json_exposes_translations_stable_ids_and_profile_urls(self):
        response = self.client.get(reverse("companies_api:public-company-json", args=[self.org.slug]))
        payload = response.json()
        self.assertEqual(payload["company"]["id"], str(self.org.public_id))
        self.assertEqual(payload["discovery"]["products"][0]["id"], str(self.product.public_id))
        self.assertEqual(set(payload["ai_access"]["profile_urls"]), {"en", "pl"})
        self.assertEqual(set(payload["discovery"]["products"][0]["translations"]), {"en", "pl"})

    def test_sitemap_contains_language_pages_and_hreflang(self):
        response = self.client.get(reverse("sitemap-xml"))
        body = response.content.decode()
        self.assertContains(response, f"/companies/{self.org.slug}/en/")
        self.assertContains(response, f"/companies/{self.org.slug}/pl/")
        self.assertIn('hreflang="x-default"', body)
        self.assertNotIn(f"/api/public/{self.org.slug}/company.json", body)

    def test_api_discovery_documents_language_endpoints(self):
        guide = self.client.get(reverse("api-guide-txt"))
        robots = self.client.get(reverse("robots-txt"))
        schema = self.client.get(reverse("openapi-schema"), HTTP_ACCEPT="application/json")
        swagger = self.client.get(reverse("api-schema-swagger-ui"))
        redoc = self.client.get(reverse("api-schema-redoc"))

        self.assertContains(guide, "/{slug}/{language_code}/company.json")
        self.assertContains(guide, "Supported language codes: en, pl, de, es, it, fr")
        self.assertContains(robots, "every published profile language")
        self.assertContains(robots, reverse("sitemap-xml"))
        self.assertEqual(swagger.status_code, 200)
        self.assertEqual(redoc.status_code, 200)

        localized_path = "/api/public/{slug}/{language_code}/company.json"
        operation = schema.json()["paths"][localized_path]["get"]
        parameter = next(item for item in operation["parameters"] if item["name"] == "language_code")
        self.assertEqual(set(parameter["schema"]["enum"]), {"en", "pl", "de", "es", "it", "fr"})
        self.assertIn("only customer content authored", operation["description"])

    def test_directory_and_api_search_include_translation_maps(self):
        self.org.descriptions_by_language["pl"]["long"] = "Wyjątkowa fraza katalogowa"
        self.org.save(update_fields=["descriptions_by_language"])
        ContentEntry.objects.create(
            organization=self.org,
            entry_type="faq",
            title="Question",
            questions_by_language={"pl": "Jak działa wyszukiwanie?"},
            answers_by_language={"pl": "Odpowiedź indeksowana w katalogu."},
        )
        for phrase in ("Wyjątkowa fraza", "Polski produkt", "Odpowiedź indeksowana"):
            with self.subTest(phrase=phrase):
                directory = self.client.get(reverse("public-company-directory"), {"q": phrase})
                catalog = self.client.get(reverse("companies_api:company-list"), {"search": phrase})
                self.assertContains(directory, self.org.name)
                self.assertEqual(catalog.status_code, 200)
                self.assertEqual(catalog.json()["results"][0]["slug"], self.org.slug)
