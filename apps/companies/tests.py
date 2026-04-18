from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils.translation import override
from rest_framework.test import APIClient

from apps.accounts.models import UserPlanTier
from apps.companies.forms import OrganizationForm
from apps.companies.models import ContentEntry, Organization, OrganizationType, Product, SocialProfile, Tag, VerificationStatus
from apps.subscriptions.models import PlanTier


User = get_user_model()


class CompanyApiTests(TestCase):
    def setUp(self):
        self.api_client = APIClient()
        self.user = User.objects.create_user(
            username="owner",
            email="owner@example.com",
            password="strong-pass-123",
        )
        self.other_user = User.objects.create_user(
            username="other",
            email="other@example.com",
            password="strong-pass-123",
        )

    def create_organization(self, owner=None, **kwargs):
        defaults = {
            "name": "Acme AI",
            "slug": "acme-ai",
            "short_description_en": "AI-ready company profile.",
            "public": True,
            "allow_ai_indexing": True,
        }
        defaults.update(kwargs)
        return Organization.objects.create(owner=owner or self.user, **defaults)

    def test_organization_gets_basic_subscription_by_default(self):
        organization = self.create_organization()
        self.assertEqual(organization.get_subscription().tier, PlanTier.BASIC)

    def test_basic_public_feed_is_available(self):
        organization = self.create_organization()

        response = self.api_client.get(f"/api/public/{organization.slug}/company.json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["company"]["slug"], organization.slug)

    def test_jsonld_feed_is_hidden_for_basic_plan(self):
        organization = self.create_organization()

        response = self.api_client.get(f"/api/public/{organization.slug}/company.jsonld")

        self.assertEqual(response.status_code, 404)

    def test_basic_feed_includes_full_company_discovery_payload(self):
        organization = self.create_organization(
            short_description_pl="Krótki opis po polsku.",
            long_description_en="Long company profile for AI systems.",
            long_description_pl="Pełny opis firmy dla systemów AI.",
            website_url="https://acme.example",
            phone_number="+48 123 456 789",
            address_line="Main Street 1",
            city="Warsaw",
            postal_code="00-001",
            country="Poland",
            primary_language="pl",
            content_languages=["pl", "en"],
        )
        SocialProfile.objects.create(organization=organization, network="linkedin", url="https://linkedin.com/company/acme")
        Tag.objects.create(organization=organization, name="ai search", language="en")
        Product.objects.create(
            organization=organization,
            name="Visibility Audit",
            short_description_en="Audit for AI discoverability.",
            product_url="https://acme.example/audit",
            price_from="1999.00",
            currency="PLN",
            is_featured=True,
        )
        ContentEntry.objects.create(
            organization=organization,
            entry_type="guide",
            title="How we improve AI visibility",
            summary_en="Guide for better discoverability.",
            content_url="https://acme.example/guide",
        )

        response = self.api_client.get(f"/api/public/{organization.slug}/company.json")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["company"]["languages"]["declared_content_languages"], ["pl", "en"])
        self.assertEqual(payload["company"]["descriptions"]["pl"]["long"], "Pełny opis firmy dla systemów AI.")
        self.assertEqual(payload["discovery"]["social_profiles"][0]["network"], "linkedin")
        self.assertEqual(payload["discovery"]["tags"][0]["name"], "ai search")
        self.assertEqual(payload["discovery"]["products"][0]["name"], "Visibility Audit")
        self.assertEqual(payload["discovery"]["content_entries"][0]["title"], "How we improve AI visibility")

    def test_basic_feed_includes_provenance_and_verification_fields(self):
        organization = self.create_organization(
            website_url="https://acme.example",
            verification_status=VerificationStatus.HUMAN_ADMIN_VERIFIED,
        )

        response = self.api_client.get(f"/api/public/{organization.slug}/company.json")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["provenance"]["source_type"], "user_submitted")
        self.assertEqual(payload["provenance"]["source_url"], "https://acme.example")
        self.assertEqual(payload["provenance"]["verification_status"], "human_admin_verified")

    def test_source_url_tracks_current_website_url(self):
        organization = self.create_organization(website_url="https://old.example")
        organization.website_url = "https://new.example"
        organization.save()

        organization.refresh_from_db()
        self.assertEqual(organization.source_url, "https://new.example")

    def test_multiple_organizations_get_separate_feed_urls_by_slug(self):
        first = self.create_organization(name="Alpha", slug="alpha")
        second = self.create_organization(name="Beta", slug="beta")

        first_response = self.api_client.get(f"/api/public/{first.slug}/company.json")
        second_response = self.api_client.get(f"/api/public/{second.slug}/company.json")

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(first_response.json()["company"]["slug"], "alpha")
        self.assertEqual(second_response.json()["company"]["slug"], "beta")

    def test_organization_list_is_limited_to_owner(self):
        owned = self.create_organization(name="Owned", slug="owned")
        self.create_organization(owner=self.other_user, name="Other", slug="other")
        self.api_client.force_authenticate(self.user)

        response = self.api_client.get("/api/organizations/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(response.json()[0]["id"], owned.id)

    def test_basic_plan_blocks_tag_creation(self):
        organization = self.create_organization()
        self.api_client.force_authenticate(self.user)

        response = self.api_client.post(
            f"/api/organizations/{organization.id}/tags/",
            {"name": "ai-seo", "language": "en"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("plan", response.json())

    def test_plus_plan_allows_tag_creation(self):
        self.user.plan_tier = UserPlanTier.PLUS
        self.user.save(update_fields=["plan_tier"])
        organization = self.create_organization(slug="plus-company")
        self.api_client.force_authenticate(self.user)

        response = self.api_client.post(
            f"/api/organizations/{organization.id}/tags/",
            {"name": "ai-seo", "language": "en"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["name"], "ai-seo")

    def test_basic_plan_blocks_second_organization_creation(self):
        self.create_organization(name="One", slug="one")
        self.api_client.force_authenticate(self.user)

        response = self.api_client.post(
            "/api/organizations/",
            {
                "name": "Two",
                "slug": "two",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("plan", response.json())

    def test_plus_plan_allows_three_organizations_and_blocks_fourth(self):
        self.user.plan_tier = UserPlanTier.PLUS
        self.user.save(update_fields=["plan_tier"])
        self.create_organization(name="One", slug="plus-one")
        self.create_organization(name="Two", slug="plus-two")
        self.api_client.force_authenticate(self.user)

        third_response = self.api_client.post(
            "/api/organizations/",
            {
                "name": "Three",
                "slug": "plus-three",
            },
            format="json",
        )
        fourth_response = self.api_client.post(
            "/api/organizations/",
            {
                "name": "Four",
                "slug": "plus-four",
            },
            format="json",
        )

        self.assertEqual(third_response.status_code, 201)
        self.assertEqual(fourth_response.status_code, 400)
        self.assertIn("plan", fourth_response.json())


class OrganizationFormLocalizationTests(TestCase):
    def test_polish_labels_are_used_for_polish_language(self):
        with override("pl"):
            form = OrganizationForm()

        self.assertEqual(form.fields["name"].label, "Nazwa firmy")
        self.assertEqual(form.fields["company_type"].label, "Typ firmy")
        self.assertEqual(form.fields["website_url"].label, "Adres strony WWW")
        self.assertEqual(form.fields["ai_summary"].label, "Dla jakich klient\u00f3w/projekt\u00f3w ta firma jest najlepsza?")
        self.assertNotIn("slug", form.fields)
        self.assertNotIn("legal_name", form.fields)
        self.assertEqual(
            form.fields["company_type"].choices,
            [
                (OrganizationType.MANUFACTURING, "Produkcyjna"),
                (OrganizationType.SERVICES, "Usługowa"),
                (OrganizationType.TRADING, "Handlowa"),
                (OrganizationType.OTHER, "Inna"),
            ],
        )

    def test_website_url_accepts_value_without_scheme(self):
        form = OrganizationForm(
            data={
                "name": "Acme AI",
                "company_type": OrganizationType.SERVICES,
                "website_url": "acme.ai",
                "content_languages": '["pl"]',
                "short_description_pl": "Krótki opis firmy.",
                "long_description_pl": "Pełny opis firmy do walidacji formularza.",
            },
            language_code="pl",
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["website_url"], "https://acme.ai")

    def test_form_exposes_dedicated_feed_languages(self):
        form = OrganizationForm(language_code="pl")

        self.assertEqual(form.AVAILABLE_LANGUAGES, ["pl", "en", "de", "es", "it", "fr"])

    def test_form_saves_language_specific_keywords(self):
        owner = User.objects.create_user(
            username="lang-owner",
            email="lang-owner@example.com",
            password="strong-pass-123",
        )
        form = OrganizationForm(
            data={
                "name": "Acme Multilang",
                "company_type": OrganizationType.SERVICES,
                "website_url": "acme.ai",
                "primary_language": "pl",
                "content_languages": '["pl", "es"]',
                "short_description_pl": "Opis PL",
                "long_description_pl": "Długi opis PL",
                "short_description_es": "Descripcion ES",
                "long_description_es": "Descripcion larga ES",
                "tags_pl": "widocznosc ai, pozycjonowanie",
                "tags_es": "visibilidad ai, seo local",
            },
            language_code="pl",
        )

        self.assertTrue(form.is_valid(), form.errors)
        form.instance.owner = owner
        organization = form.save()

        tags = set(organization.tags.values_list("name", "language"))
        self.assertIn(("widocznosc ai", "pl"), tags)
        self.assertIn(("pozycjonowanie", "pl"), tags)
        self.assertIn(("visibilidad ai", "es"), tags)
        self.assertIn(("seo local", "es"), tags)

    def test_form_persists_selected_default_feed_language(self):
        owner = User.objects.create_user(
            username="default-lang-owner",
            email="default-lang-owner@example.com",
            password="strong-pass-123",
        )
        form = OrganizationForm(
            data={
                "name": "Acme ES",
                "company_type": OrganizationType.SERVICES,
                "website_url": "acme.ai",
                "primary_language": "es",
                "content_languages": '["pl", "es"]',
                "short_description_pl": "Opis PL",
                "long_description_pl": "Długi opis PL",
                "short_description_es": "Descripcion ES",
                "long_description_es": "Descripcion larga ES",
            },
            language_code="pl",
        )

        self.assertTrue(form.is_valid(), form.errors)
        form.instance.owner = owner
        organization = form.save()

        self.assertEqual(organization.primary_language, "es")

