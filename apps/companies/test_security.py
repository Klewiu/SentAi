import json
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.accounts.models import User
from .forms import OrganizationForm
from .models import Organization, Product, VerificationStatus


class PublishingSecurityTests(TestCase):
    def test_bulk_json_stream_is_valid_and_contains_only_public_companies(self):
        Organization.objects.create(owner=self.user, name="Private", public=False)
        response = self.client.get(reverse("companies_api:public-all-json"))
        self.assertTrue(response.streaming)
        data = json.loads(b"".join(response.streaming_content))
        self.assertEqual(data["meta"]["total"], 1)
        self.assertEqual(len(data["results"]), 1)

    def test_updates_are_bounded_and_offer_the_next_page(self):
        Organization.objects.bulk_create([Organization(owner=self.user, name=f"Company {index}", slug=f"company-{index}", verification_status=VerificationStatus.HUMAN_ADMIN_VERIFIED) for index in range(100)])
        response = self.client.get(reverse("companies_api:company-updates"))
        self.assertEqual(len(response.json()["results"]), 100)
        self.assertEqual(response.json()["total"], 101)
        self.assertEqual(response.json()["next_page"], 2)
    def setUp(self):
        self.user = User.objects.create_user(username="audit", email="private@example.com", password="test-password", plan_tier="PRO", plan_selected_at=timezone.now())
        self.org = Organization.objects.create(owner=self.user, name="Audit", contact_email="public@example.com", verification_status=VerificationStatus.HUMAN_ADMIN_VERIFIED)

    def test_public_contact_is_explicit_business_email(self):
        data = self.client.get(reverse("companies_api:public-company-json", args=[self.org.slug])).json()
        self.assertEqual(data["company"]["contact"]["email"], "public@example.com")
        self.assertNotIn("private@example.com", json.dumps(data))

    def test_jsonld_cannot_escape_script_element(self):
        payload = '</script><script>alert("audit")</script>'
        Organization.objects.filter(pk=self.org.pk).update(name=payload)
        response = self.client.get(reverse("public-company-detail", args=[self.org.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(payload, response.content.decode())
        self.assertIn("\\u003C/script", response.content.decode())

    def test_material_edit_preserves_existing_verification(self):
        self.client.force_login(self.user)
        response = self.client.patch(reverse("companies_api:organization-detail", args=[self.org.pk]), json.dumps({"name": "Changed identity"}), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.org.refresh_from_db()
        self.assertEqual(self.org.verification_status, VerificationStatus.HUMAN_ADMIN_VERIFIED)

    def test_related_content_edit_preserves_existing_verification(self):
        from .models import Product
        product = Product.objects.create(organization=self.org, name="Original")
        Organization.objects.filter(pk=self.org.pk).update(
            verification_status=VerificationStatus.HUMAN_ADMIN_VERIFIED
        )
        product.name = "Updated"
        product.save()
        self.org.refresh_from_db()
        self.assertEqual(self.org.verification_status, VerificationStatus.HUMAN_ADMIN_VERIFIED)

    def form(self):
        return OrganizationForm(data={"name": "Audit", "website_url": "https://example.com", "company_type": "services", "primary_language": "en", "content_languages": '["en"]', "products_en": "Product", "tags_en": "tag"}, instance=self.org, organization=self.org)

    def test_basic_form_cannot_bypass_api_limits(self):
        self.user.plan_tier = "BASIC"
        self.user.save()
        self.assertFalse(self.form().is_valid())

    def test_downgrade_after_validation_cannot_save_premium_resources(self):
        from django.core.exceptions import ValidationError
        form = self.form()
        self.assertTrue(form.is_valid(), form.errors)
        User.objects.filter(pk=self.user.pk).update(plan_tier="BASIC")
        with self.assertRaises(ValidationError):
            form.save()
        self.assertFalse(self.org.products.exists())

    def test_catalog_etag_revalidates_and_changes_on_downgrade(self):
        from .models import Tag
        Tag.objects.create(organization=self.org, name="paid-tag")
        Organization.objects.filter(pk=self.org.pk).update(verification_status=VerificationStatus.HUMAN_ADMIN_VERIFIED)
        url = reverse("companies_api:company-detail", args=[self.org.slug])
        first = self.client.get(url)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(self.client.get(url, HTTP_IF_NONE_MATCH=first["ETag"]).status_code, 304)
        User.objects.filter(pk=self.user.pk).update(plan_tier="BASIC")
        changed = self.client.get(url, HTTP_IF_NONE_MATCH=first["ETag"])
        self.assertEqual(changed.status_code, 404)
        self.assertNotIn("ETag", changed)
        self.assertNotIn("paid-tag", changed.content.decode())

    def test_form_preserves_product_id_and_price(self):
        product = Product.objects.create(organization=self.org, name="Product", price_from="150.00", currency="EUR")
        form = self.form()
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        product.refresh_from_db()
        self.assertEqual(str(product.price_from), "150.00")
        self.assertEqual(product.currency, "EUR")

    def test_product_edit_preserves_verification_and_updates_freshness(self):
        product = Product.objects.create(organization=self.org, name="Product")
        Organization.objects.filter(pk=self.org.pk).update(verification_status=VerificationStatus.HUMAN_ADMIN_VERIFIED)
        self.org.refresh_from_db()
        before = self.org.updated_at
        self.client.force_login(self.user)
        response = self.client.patch(reverse("companies_api:product-detail", args=[self.org.pk, product.pk]), json.dumps({"name": "Changed"}), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.org.refresh_from_db()
        self.assertGreater(self.org.updated_at, before)
        self.assertTrue(self.org.is_verified)
