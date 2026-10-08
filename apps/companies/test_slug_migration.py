from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class GeneratedOrganizationSlugMigrationTests(TransactionTestCase):
    migrate_from = ("companies", "0021_product_product_type")
    migrate_to = ("companies", "0022_transliterate_generated_organization_slugs")

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        Organization = old_apps.get_model("companies", "Organization")
        user = get_user_model().objects.create_user(
            username="slug-migration",
            email="slug-migration@example.com",
            password="migration-test-password",
        )
        self.generated_org_id = Organization.objects.create(
            owner_id=user.pk,
            name="Ats Display Spółka z ograniczoną odpowiedzialnością",
            slug="ats-display-spoka-z-ograniczona-odpowiedzialnoscia",
        ).pk
        self.custom_org_id = Organization.objects.create(
            owner_id=user.pk,
            name="Łódź company",
            slug="my-custom-url",
        ).pk
        Organization.objects.create(
            owner_id=user.pk,
            name="Ats Display Spółka z ograniczoną odpowiedzialnością Copy",
            slug="ats-display-spolka-z-ograniczona-odpowiedzialnoscia",
        )

        MigrationExecutor(connection).migrate([self.migrate_to])

    def tearDown(self):
        MigrationExecutor(connection).migrate(
            MigrationExecutor(connection).loader.graph.leaf_nodes()
        )
        super().tearDown()

    def test_only_generated_legacy_slugs_are_transliterated_without_collisions(self):
        from .models import Organization

        generated = Organization.objects.get(pk=self.generated_org_id)
        custom = Organization.objects.get(pk=self.custom_org_id)
        self.assertEqual(
            generated.slug,
            "ats-display-spolka-z-ograniczona-odpowiedzialnoscia-2",
        )
        self.assertEqual(custom.slug, "my-custom-url")
