from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils.translation import override
from rest_framework.test import APIClient

from apps.accounts.models import UserPlanTier
from apps.companies.forms import OrganizationForm
from apps.companies.models import Organization, OrganizationType
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
        organization = self.create_organization(slug="plus-company")
        subscription = organization.get_subscription()
        subscription.tier = PlanTier.PLUS
        subscription.save(update_fields=["tier", "updated_at"])
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

