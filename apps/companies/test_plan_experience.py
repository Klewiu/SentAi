import json
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.accounts.models import User
from .forms import OrganizationForm
from .models import ContentEntry, EntryType, Organization, Product, SocialProfile, Tag, VerificationStatus
from .services import build_basic_feed
from apps.subscriptions.models import PLAN_FEATURES, PlanTier


class PlanExperienceTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="profile", email="profile@example.com", password="test-password", plan_selected_at=timezone.now(), plan_tier="PRO")
        self.org = Organization.objects.create(owner=self.owner, name="Profile", website_url="https://example.com", content_languages=["en"], primary_language="en")
        self.product = Product.objects.create(organization=self.org, name="Premium product", price_from=120, currency="EUR")
        Tag.objects.create(organization=self.org, name="Specialty")
        Organization.objects.filter(pk=self.org.pk).update(verification_status=VerificationStatus.HUMAN_ADMIN_VERIFIED)

    def test_every_plan_has_every_format_with_appropriate_content(self):
        for tier in ("BASIC", "PLUS", "PRO"):
            User.objects.filter(pk=self.owner.pk).update(plan_tier=tier)
            from apps.billing.models import BillingSubscription
            from datetime import timedelta
            BillingSubscription.objects.update_or_create(user=self.owner, defaults={"tier": tier, "status": "active", "current_period_end": timezone.now() + timedelta(days=365)})
            for suffix in ("json", "jsonld", "md", "llms"):
                with self.subTest(tier=tier, format=suffix):
                    response = self.client.get(reverse(f"companies_api:public-company-{suffix}", args=[self.org.slug]))
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual("Premium product" in response.content.decode(), tier == "PRO")
            index = self.client.get(reverse("site-llms-txt"))
            self.assertContains(index, self.org.slug)
        Organization.objects.filter(pk=self.org.pk).update(public=False)
        for suffix in ("json", "jsonld", "md", "llms"):
            self.assertEqual(self.client.get(reverse(f"companies_api:public-company-{suffix}", args=[self.org.slug])).status_code, 404)

    def payload(self, **kwargs):
        return {"name": "Profile", "website_url": "example.com", "company_type": "services", "primary_language": "en", "content_languages": '["en"]', "short_description_en": "We provide accounting for local businesses.", **kwargs}

    def test_validation_error_retains_new_language_and_text(self):
        form = OrganizationForm(instance=self.org, organization=self.org, data=self.payload(name="", content_languages='["en", "es"]', short_description_es="Contabilidad local"))
        self.assertFalse(form.is_valid())
        self.assertEqual(form.selected_languages, ["en", "es"])
        self.assertEqual(next(row for row in form.language_sections if row["code"] == "es")["short"], "Contabilidad local")

    def test_structured_product_inputs_preserve_price_and_allow_punctuation(self):
        form = OrganizationForm(instance=self.org, organization=self.org, data=self.payload(product_rows_en=json.dumps([{"name": "Premium product", "description": "Reports, payroll | tailored support", "url": "https://example.com/product"}])))
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.product.refresh_from_db()
        self.assertEqual(self.product.price_from, 120)
        self.assertEqual(self.product.localized_summary("en"), "Reports, payroll | tailored support")

    def test_free_form_save_preserves_hidden_paid_content(self):
        self.owner.plan_tier = "BASIC"
        self.owner.save()
        form = OrganizationForm(instance=self.org, organization=self.org, data=self.payload())
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())
        self.assertTrue(self.org.tags.exists())

    def test_form_checkbox_submission_saves_selected_language(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("dashboard:organization-edit", args=[self.org.pk]), self.payload(languages=["es"], primary_language="es", short_description_es="Contabilidad local"))
        self.assertEqual(response.status_code, 302)
        self.org.refresh_from_db()
        self.assertEqual(self.org.content_languages, ["es"])
        self.assertEqual(self.org.localized_text("short_description", "es"), "Contabilidad local")

    def test_downgrade_preserves_stored_translations(self):
        self.org.content_languages = ["en", "es", "pl"]
        self.org.descriptions_by_language = {"en": {"short": "Accounting"}, "es": {"short": "Contabilidad"}, "pl": {"short": "Finanse"}}
        self.org.save()
        User.objects.filter(pk=self.owner.pk).update(plan_tier="BASIC")
        form = OrganizationForm(instance=self.org, organization=self.org, data=self.payload())
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.org.refresh_from_db()
        self.assertEqual(self.org.descriptions_by_language["es"]["short"], "Contabilidad")

    def test_free_form_hides_paid_inputs_and_uses_interface_language(self):
        self.owner.plan_tier = "BASIC"
        self.owner.save()
        self.client.force_login(self.owner)
        response = self.client.get(reverse("dashboard:organization-edit", args=[self.org.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="products_en"')
        self.assertNotContains(response, 'name="featured_entry_summary"')
        self.assertContains(response, 'name="short_description_en"')
        self.assertEqual(OrganizationForm(language_code="en").selected_languages, ["en"])

    def test_form_rejects_non_web_schemes_with_useful_message(self):
        form = OrganizationForm(instance=self.org, organization=self.org, data=self.payload(website_url="ftp://example.com"))
        self.assertFalse(form.is_valid())
        self.assertIn("website_url", form.errors)

    def test_overlong_translation_identifies_language_and_keeps_value(self):
        form = OrganizationForm(instance=self.org, organization=self.org, data=self.payload(short_description_en="x" * 281))
        self.assertFalse(form.is_valid())
        section = next(section for section in form.language_sections if section["code"] == "en")
        self.assertIn("280", section["error"])
        self.assertEqual(section["short"], "x" * 281)

    def test_product_editor_does_not_fill_missing_translation_from_another_language(self):
        self.org.primary_language = "pl"
        self.org.content_languages = ["pl", "en"]
        self.org.save()
        self.product.language = "pl"
        self.product.name = "Polski produkt"
        self.product.short_description_pl = "Polski opis produktu"
        self.product.save()
        form = OrganizationForm(instance=self.org, organization=self.org)
        sections = {row["code"]: row for row in form.language_sections}
        self.assertEqual(sections["pl"]["product_rows"][0]["name"], "Polski produkt")
        self.assertEqual(sections["en"]["product_rows"], [])

    def test_faq_editor_does_not_fill_missing_translation_from_another_language(self):
        self.org.primary_language = "pl"
        self.org.content_languages = ["pl", "de"]
        self.org.save()
        entry = ContentEntry.objects.create(
            organization=self.org,
            entry_type=EntryType.FAQ,
            title="Polskie pytanie?",
            questions_by_language={"pl": "Polskie pytanie?"},
            answers_by_language={"pl": "Polska odpowiedź."},
        )

        form = OrganizationForm(instance=self.org, organization=self.org)

        sections = {row["code"]: row for row in form.language_sections}
        self.assertEqual(sections["pl"]["faq_rows"][0]["question"], "Polskie pytanie?")
        self.assertEqual(sections["de"]["faq_rows"], [])

    def test_products_and_urls_are_saved_independently_per_language(self):
        payload = self.payload(
            content_languages='["en", "pl"]',
            product_rows_en=json.dumps([{
                "id": self.product.pk,
                "product_type": "service",
                "name": "Garden design",
                "description": "English service",
                "url": "https://example.com/en/garden",
            }]),
            product_rows_pl=json.dumps([{
                "product_type": "product",
                "name": "Projekt ogrodu",
                "description": "Polska usluga",
                "url": "https://example.com/pl/ogrod",
            }]),
        )
        form = OrganizationForm(instance=self.org, organization=self.org, data=payload)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.product.refresh_from_db()
        polish_product = self.org.products.get(language="pl")
        self.assertEqual(self.product.name, "Garden design")
        self.assertEqual(self.product.language, "en")
        self.assertEqual(self.product.product_type, Product.ProductType.SERVICE)
        self.assertEqual(self.product.product_url, "https://example.com/en/garden")
        self.assertEqual(polish_product.name, "Projekt ogrodu")
        self.assertEqual(polish_product.product_type, Product.ProductType.PRODUCT)
        self.assertEqual(polish_product.product_url, "https://example.com/pl/ogrod")
        self.assertNotEqual(self.product.pk, polish_product.pk)
        self.assertEqual(polish_product.translation_for_editor("pl")["description"], "Polska usluga")
        self.assertEqual(self.product.price_from, 120)

    def test_product_type_defaults_to_product_and_rejects_unknown_form_values(self):
        valid_form = OrganizationForm(
            instance=self.org,
            organization=self.org,
            data=self.payload(product_rows_en=json.dumps([{
                "name": "Garden tool",
                "description": "",
                "url": "",
            }])),
        )
        self.assertTrue(valid_form.is_valid(), valid_form.errors)
        valid_form.save()
        self.product.refresh_from_db()
        self.assertEqual(self.product.product_type, Product.ProductType.PRODUCT)

        invalid_form = OrganizationForm(
            instance=self.org,
            organization=self.org,
            data=self.payload(product_rows_en=json.dumps([{
                "name": "Garden tool",
                "description": "",
                "url": "",
                "product_type": "bundle",
            }])),
        )
        self.assertFalse(invalid_form.is_valid())

    def test_customer_form_saves_independent_faqs_per_language(self):
        english = [
            {"id": None, "question": "Where do you work?", "answer": "We serve Greater London.", "url": "https://example.com/area"},
            {"id": None, "question": "How do I order?", "answer": "Send us the project brief.", "url": ""},
        ]
        polish = [
            {"id": None, "question": "Gdzie działacie?", "answer": "Obsługujemy cały Kraków.", "url": "https://example.com/area"},
            {"id": None, "question": "Jak zamówić?", "answer": "Wyślij nam opis projektu.", "url": ""},
        ]
        form = OrganizationForm(
            instance=self.org,
            organization=self.org,
            data=self.payload(
                content_languages='["en", "pl"]',
                short_description_pl="Pomagamy lokalnym firmom.",
                faq_rows_en=json.dumps(english),
                faq_rows_pl=json.dumps(polish),
            ),
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        english_entries = list(self.org.content_entries.filter(entry_type=EntryType.FAQ, language="en"))
        polish_entries = list(self.org.content_entries.filter(entry_type=EntryType.FAQ, language="pl"))
        self.assertEqual(len(english_entries), 2)
        self.assertEqual(len(polish_entries), 2)
        english_by_question = {entry.localized_question("en"): entry for entry in english_entries}
        polish_by_question = {entry.localized_question("pl"): entry for entry in polish_entries}
        english_area = english_by_question["Where do you work?"]
        polish_area = polish_by_question["Gdzie działacie?"]
        self.assertNotEqual(english_area.pk, polish_area.pk)
        self.assertEqual(english_area.localized_question("pl"), "")
        self.assertEqual(polish_area.localized_question("en"), "")
        self.assertEqual(english_area.localized_answer("en"), "We serve Greater London.")
        self.assertEqual(polish_area.localized_answer("pl"), "Obsługujemy cały Kraków.")
        self.assertEqual(english_area.content_url, "https://example.com/area")
        self.assertEqual(polish_area.content_url, "https://example.com/area")
        self.assertTrue(english_area.is_featured)
        self.assertTrue(polish_area.is_featured)

        english[0]["answer"] = "We serve London and nearby towns."
        english[0]["url"] = "https://example.com/en/area"
        updated_form = OrganizationForm(
            instance=self.org,
            organization=self.org,
            data=self.payload(
                content_languages='["en", "pl"]',
                faq_rows_en=json.dumps([
                    {**english[0], "id": english_area.pk},
                    {**english[1], "id": english_by_question["How do I order?"].pk},
                ]),
                faq_rows_pl=json.dumps([
                    {**polish[0], "id": polish_area.pk},
                    {**polish[1], "id": polish_by_question["Jak zamówić?"].pk},
                ]),
            ),
        )
        self.assertTrue(updated_form.is_valid(), updated_form.errors)
        updated_form.save()
        english_area.refresh_from_db()
        polish_area.refresh_from_db()
        self.assertEqual(english_area.localized_answer("en"), "We serve London and nearby towns.")
        self.assertEqual(english_area.content_url, "https://example.com/en/area")
        self.assertEqual(polish_area.localized_answer("pl"), "Obsługujemy cały Kraków.")
        self.assertEqual(polish_area.content_url, "https://example.com/area")

    def test_pro_form_accepts_five_faqs_and_rejects_six(self):
        rows = [
            {"id": None, "question": f"Question {index}?", "answer": f"Answer {index}.", "url": ""}
            for index in range(1, 6)
        ]
        valid_form = OrganizationForm(
            instance=self.org,
            organization=self.org,
            data=self.payload(faq_rows_en=json.dumps(rows)),
        )
        self.assertTrue(valid_form.is_valid(), valid_form.errors)
        invalid_form = OrganizationForm(
            instance=self.org,
            organization=self.org,
            data=self.payload(faq_rows_en=json.dumps(rows + [{"question": "Too many?", "answer": "Yes.", "url": ""}])),
        )
        self.assertFalse(invalid_form.is_valid())

    def test_plus_form_and_feed_limit_social_profiles_and_faqs(self):
        User.objects.filter(pk=self.owner.pk).update(plan_tier="PLUS")
        self.owner.refresh_from_db()
        valid_form = OrganizationForm(
            instance=self.org,
            organization=self.org,
            data=self.payload(
                social_profiles_text="linkedin.com/company/profile\nhttps://facebook.com/profile",
            ),
        )
        self.assertTrue(valid_form.is_valid(), valid_form.errors)

        form = OrganizationForm(
            instance=self.org,
            organization=self.org,
            data=self.payload(
                social_profiles_text="linkedin.com/company/profile\nhttps://example.com/second",
                faq_rows_en=json.dumps([{"question": "Question?", "answer": "Answer.", "url": ""}]),
            ),
        )
        self.assertFalse(form.is_valid())

        SocialProfile.objects.create(organization=self.org, network="linkedin", url="https://linkedin.com/company/profile")
        SocialProfile.objects.create(organization=self.org, network="facebook", url="https://facebook.com/profile")
        ContentEntry.objects.create(
            organization=self.org,
            entry_type=EntryType.FAQ,
            title="Question?",
            questions_by_language={"en": "Question?"},
            answers_by_language={"en": "Answer."},
        )
        payload = build_basic_feed(self.org)
        self.assertEqual(len(payload["discovery"]["social_profiles"]), 2)
        self.assertNotIn("content_entries", payload["discovery"])

    def test_faq_form_preserves_non_faq_knowledge_entries(self):
        guide = ContentEntry.objects.create(
            organization=self.org,
            entry_type=EntryType.GUIDE,
            title="Existing guide",
            summary_en="Guide summary",
        )
        form = OrganizationForm(
            instance=self.org,
            organization=self.org,
            data=self.payload(faq_rows_en=json.dumps([
                {"question": "A question?", "answer": "An answer.", "url": ""},
            ])),
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertTrue(ContentEntry.objects.filter(pk=guide.pk).exists())

    def test_agreed_customer_content_limits_are_consistent(self):
        self.assertEqual(PLAN_FEATURES[PlanTier.PLUS]["content_entries"], 0)
        self.assertEqual(PLAN_FEATURES[PlanTier.PLUS]["social_profiles"], 2)
        self.assertEqual(PLAN_FEATURES[PlanTier.PRO]["content_entries"], 5)
        self.assertEqual(PLAN_FEATURES[PlanTier.PRO]["social_profiles"], 5)
        self.assertEqual(PLAN_FEATURES[PlanTier.PRO]["tags"], 50)

    def test_plus_plan_hides_faq_entries_from_public_feed(self):
        self.org.content_languages = ["en", "pl", "es"]
        self.org.descriptions_by_language = {
            "en": {"short": "English profile"},
            "pl": {"short": "Polski profil"},
            "es": {"short": "Perfil español"},
        }
        self.org.save()
        entry = ContentEntry.objects.create(
            organization=self.org,
            entry_type=EntryType.FAQ,
            title="English question?",
            questions_by_language={"en": "English question?", "pl": "Polskie pytanie?", "es": "¿Pregunta?"},
            answers_by_language={"en": "English answer.", "pl": "Polska odpowiedź.", "es": "Respuesta."},
        )
        self.owner.plan_tier = "PLUS"
        self.owner.save(update_fields=["plan_tier"])

        payload = build_basic_feed(self.org)
        self.assertNotIn("content_entries", payload["discovery"])
        self.assertTrue(ContentEntry.objects.filter(pk=entry.pk).exists())
