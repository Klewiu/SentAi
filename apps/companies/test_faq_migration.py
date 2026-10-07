from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ContentEntryLanguageMigrationTests(TransactionTestCase):
    migrate_from = ("companies", "0019_product_language")
    migrate_to = ("companies", "0020_contententry_language")

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        Organization = old_apps.get_model("companies", "Organization")
        ContentEntry = old_apps.get_model("companies", "ContentEntry")
        user = get_user_model().objects.create_user(
            username="faq-migration",
            email="faq-migration@example.com",
            password="test-password",
        )
        organization = Organization.objects.create(
            owner_id=user.pk,
            name="Migration profile",
            slug="migration-profile",
            primary_language="en",
            content_languages=["en", "de"],
        )
        entry = ContentEntry.objects.create(
            organization_id=organization.pk,
            entry_type="faq",
            title="English question?",
            questions_by_language={
                "en": "English question?",
                "de": "Deutsche Frage?",
            },
            answers_by_language={
                "en": "English answer.",
                "de": "Deutsche Antwort.",
            },
            content_url="https://example.com/faq",
            is_featured=True,
        )
        self.original_entry_id = entry.pk
        self.original_public_id = entry.public_id

        MigrationExecutor(connection).migrate([self.migrate_to])

    def tearDown(self):
        MigrationExecutor(connection).migrate(
            MigrationExecutor(connection).loader.graph.leaf_nodes()
        )
        super().tearDown()

    def test_legacy_faq_translations_become_separate_language_entries(self):
        from .models import ContentEntry

        entries = list(
            ContentEntry.objects.filter(organization__slug="migration-profile")
            .order_by("language")
        )
        self.assertEqual([entry.language for entry in entries], ["de", "en"])
        by_language = {entry.language: entry for entry in entries}
        self.assertEqual(by_language["en"].pk, self.original_entry_id)
        self.assertEqual(by_language["en"].public_id, self.original_public_id)
        self.assertEqual(by_language["en"].questions_by_language, {"en": "English question?"})
        self.assertEqual(by_language["en"].answers_by_language, {"en": "English answer."})
        self.assertEqual(by_language["de"].questions_by_language, {"de": "Deutsche Frage?"})
        self.assertEqual(by_language["de"].answers_by_language, {"de": "Deutsche Antwort."})
        self.assertEqual(by_language["de"].content_url, "https://example.com/faq")
