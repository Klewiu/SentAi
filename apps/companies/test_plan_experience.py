import json
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.accounts.models import User
from .forms import OrganizationForm
from .models import ContentEntry, EntryType, Organization, Product, Tag, VerificationStatus
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
        self.product.name = "Polski produkt"
        self.product.short_description_pl = "Polski opis produktu"
        self.product.save()
        form = OrganizationForm(instance=self.org, organization=self.org)
        sections = {row["code"]: row for row in form.language_sections}
        self.assertEqual(sections["pl"]["product_rows"][0]["name"], "Polski produkt")
        self.assertEqual(sections["en"]["product_rows"][0]["name"], "")
        self.assertEqual(sections["en"]["product_rows"][0]["description"], "")

    def test_translated_product_names_and_descriptions_survive_save(self):
        payload = self.payload(content_languages='["en", "pl"]', product_rows_en=json.dumps([{"name": "Garden design", "description": "English service", "url": ""}]), product_rows_pl=json.dumps([{"name": "Projekt ogrodu", "description": "Polska usluga", "url": ""}]))
        form = OrganizationForm(instance=self.org, organization=self.org, data=payload)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.product.refresh_from_db()
        self.assertEqual(self.product.localized_name("en"), "Garden design")
        self.assertEqual(self.product.localized_name("pl"), "Projekt ogrodu")
        self.assertEqual(self.product.translation_for_editor("pl")["description"], "Polska usluga")
        self.assertEqual(self.product.price_from, 120)

    def test_customer_form_saves_multiple_multilingual_faqs(self):
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

        entries = list(self.org.content_entries.filter(entry_type=EntryType.FAQ).order_by("-is_featured", "pk"))
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].localized_question("en"), "Where do you work?")
        self.assertEqual(entries[0].localized_question("pl"), "Gdzie działacie?")
        self.assertEqual(entries[0].localized_answer("pl"), "Obsługujemy cały Kraków.")
        self.assertEqual(entries[0].content_url, "https://example.com/area")
        self.assertTrue(entries[0].is_featured)

    def test_pro_form_accepts_fifteen_faqs_and_rejects_sixteen(self):
        rows = [
            {"id": None, "question": f"Question {index}?", "answer": f"Answer {index}.", "url": ""}
            for index in range(1, 16)
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
        self.assertEqual(PLAN_FEATURES[PlanTier.PLUS]["content_entries"], 5)
        self.assertEqual(PLAN_FEATURES[PlanTier.PRO]["content_entries"], 15)
        self.assertEqual(PLAN_FEATURES[PlanTier.PRO]["social_profiles"], 6)
        self.assertEqual(PLAN_FEATURES[PlanTier.PRO]["tags"], 50)

    def test_downgrade_hides_archived_faq_translations_from_public_feed(self):
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

        faq = next(item for item in payload["discovery"]["content_entries"] if item["title"] == entry.title)
        self.assertEqual(faq["questions"], {"en": "English question?", "pl": "Polskie pytanie?"})
        self.assertEqual(faq["summaries"], {"en": "English answer.", "pl": "Polska odpowiedź."})
