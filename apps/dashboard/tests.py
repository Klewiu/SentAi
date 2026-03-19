from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.translation import override

from apps.accounts.models import UserPlanTier
from apps.companies.models import Organization


User = get_user_model()


class DashboardPlanLimitTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="client",
            email="client@example.com",
            password="strong-pass-123",
        )
        self.other_user = User.objects.create_user(
            username="other-client",
            email="other-client@example.com",
            password="strong-pass-123",
        )
        self.client.force_login(self.user)

    def test_add_company_button_visible_when_under_limit(self):
        response = self.client.get(reverse("dashboard:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "+ add company")

    def test_add_company_button_hidden_when_basic_limit_reached(self):
        Organization.objects.create(
            owner=self.user,
            name="Basic company",
            slug="basic-company",
        )

        response = self.client.get(reverse("dashboard:home"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Add company")

    def test_create_view_redirects_when_basic_limit_reached(self):
        Organization.objects.create(
            owner=self.user,
            name="Basic company",
            slug="basic-company",
        )

        response = self.client.get(reverse("dashboard:organization-create"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:home"))

    def test_navbar_shows_plan_user_and_counter(self):
        response = self.client.get(reverse("dashboard:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("dashboard:plan-update"))
        self.assertContains(response, "BASIC")
        self.assertContains(response, "client")
        self.assertContains(response, "0/1")

    @override_settings(
        STRIPE_SECRET_KEY="sk_test_dummy",
        SITE_BASE_URL="http://testserver",
        STRIPE_PLUS_PRICE_AMOUNT=4900,
        STRIPE_PRO_PRICE_AMOUNT=9900,
        STRIPE_CURRENCY="pln",
    )
    @patch("apps.dashboard.views.stripe.checkout.Session.create")
    def test_user_selecting_plus_starts_stripe_checkout(self, mock_checkout_create):
        mock_checkout_create.return_value = SimpleNamespace(url="https://checkout.stripe.test/session")

        response = self.client.post(
            reverse("dashboard:plan-update"),
            {"plan_tier": UserPlanTier.PLUS},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "https://checkout.stripe.test/session")
        self.user.refresh_from_db()
        self.assertEqual(self.user.plan_tier, UserPlanTier.BASIC)

    def test_user_can_downgrade_to_basic_without_payment(self):
        self.user.plan_tier = UserPlanTier.PLUS
        self.user.save(update_fields=["plan_tier"])

        response = self.client.post(
            reverse("dashboard:plan-update"),
            {"plan_tier": UserPlanTier.BASIC},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:home"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.plan_tier, UserPlanTier.BASIC)

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    @patch("apps.dashboard.views.stripe.checkout.Session.retrieve")
    def test_checkout_success_updates_user_plan(self, mock_checkout_retrieve):
        mock_checkout_retrieve.return_value = SimpleNamespace(
            metadata={"user_id": str(self.user.pk), "plan_tier": UserPlanTier.PLUS},
            payment_status="paid",
        )

        response = self.client.get(
            reverse("dashboard:plan-checkout-success"),
            {"session_id": "cs_test_123"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:home"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.plan_tier, UserPlanTier.PLUS)

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    @patch("apps.dashboard.views.stripe.checkout.Session.retrieve")
    def test_checkout_success_rejects_session_for_other_user(self, mock_checkout_retrieve):
        mock_checkout_retrieve.return_value = SimpleNamespace(
            metadata={"user_id": str(self.other_user.pk), "plan_tier": UserPlanTier.PRO},
            payment_status="paid",
        )

        response = self.client.get(
            reverse("dashboard:plan-checkout-success"),
            {"session_id": "cs_test_123"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:plan-update"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.plan_tier, UserPlanTier.BASIC)

    def test_plan_downgrade_is_blocked_if_user_has_too_many_pages(self):
        self.user.plan_tier = UserPlanTier.PLUS
        self.user.save(update_fields=["plan_tier"])
        Organization.objects.create(owner=self.user, name="A", slug="a")
        Organization.objects.create(owner=self.user, name="B", slug="b")

        response = self.client.post(
            reverse("dashboard:plan-update"),
            {"plan_tier": UserPlanTier.BASIC},
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.plan_tier, UserPlanTier.PLUS)
        self.assertContains(response, "Please reduce to 1 or fewer")

    def test_user_can_delete_own_organization(self):
        organization = Organization.objects.create(owner=self.user, name="Delete me", slug="delete-me")

        response = self.client.post(reverse("dashboard:organization-delete", args=[organization.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:home"))
        self.assertFalse(Organization.objects.filter(pk=organization.pk).exists())

    def test_user_cannot_delete_other_user_organization(self):
        foreign_organization = Organization.objects.create(
            owner=self.other_user,
            name="Foreign",
            slug="foreign",
        )

        response = self.client.post(reverse("dashboard:organization-delete", args=[foreign_organization.pk]))

        self.assertEqual(response.status_code, 404)
        self.assertTrue(Organization.objects.filter(pk=foreign_organization.pk).exists())


class LanguageSwitchTests(TestCase):
    def test_localized_set_language_route_has_polish_prefix(self):
        self.assertEqual(reverse("set_language_localized"), "/set-language/")

        with override("pl"):
            self.assertEqual(reverse("set_language_localized"), "/pl/set-language/")

    def test_switching_from_polish_url_to_english_removes_prefix(self):
        response = self.client.post(
            "/pl/set-language/",
            {"language": "en", "next": "/pl/organizations/new/"},
            HTTP_HOST="testserver",
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/organizations/new/")
        self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, "en")

    def test_switching_from_english_url_to_polish_adds_prefix(self):
        response = self.client.post(
            "/set-language/",
            {"language": "pl", "next": "/organizations/new/"},
            HTTP_HOST="testserver",
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/pl/organizations/new/")
        self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, "pl")
