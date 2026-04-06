from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from django.urls import reverse
from django.utils.translation import override

from apps.accounts.models import AccountType, UserPlanTier
from apps.companies.models import Organization
from apps.sales.models import ProspectActivity, ProspectClient, SellerSettlement


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
        self.assertContains(response, reverse("dashboard:organization-create"))

    def test_add_company_button_hidden_when_basic_limit_reached(self):
        Organization.objects.create(
            owner=self.user,
            name="Basic company",
            slug="basic-company",
        )

        response = self.client.get(reverse("dashboard:home"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse("dashboard:organization-create"))

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


class SellerManagementTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="strong-pass-123",
        )
        self.client_user = User.objects.create_user(
            username="client",
            email="client@example.com",
            password="strong-pass-123",
        )
        self.seller = User.objects.create_user(
            username="seller-home",
            email="seller-home@example.com",
            password="strong-pass-123",
            account_type=AccountType.STAFF,
        )

    def test_seller_sees_dedicated_home_layout(self):
        self.client.force_login(self.seller)

        response = self.client.get(reverse("dashboard:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("dashboard:seller-clients"))
        self.assertContains(response, reverse("dashboard:seller-prospects"))
        self.assertNotContains(response, reverse("dashboard:plan-update"))
        self.assertNotContains(response, "0/1")

    def test_admin_can_open_seller_list(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("dashboard:seller-list"))

        self.assertEqual(response.status_code, 200)

    def test_admin_home_shows_reports_quick_action(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("dashboard:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("dashboard:report-seller-activities"))

    def test_admin_report_shows_activity_counts_and_month_filter(self):
        other_seller = User.objects.create_user(
            username="seller-second",
            email="seller-second@example.com",
            password="strong-pass-123",
            account_type=AccountType.STAFF,
        )
        prospect_one = ProspectClient.objects.create(
            seller=self.seller,
            company_name="Lead One",
            contact_person="Alice",
            email="alice@example.com",
            phone="123456789",
        )
        prospect_two = ProspectClient.objects.create(
            seller=other_seller,
            company_name="Lead Two",
            contact_person="Bob",
            email="bob@example.com",
            phone="123456789",
        )
        ProspectActivity.objects.create(
            prospect=prospect_one,
            seller=self.seller,
            activity_type="call",
            activity_date="2026-03-10",
            activity_description="March call",
        )
        ProspectActivity.objects.create(
            prospect=prospect_one,
            seller=self.seller,
            activity_type="email",
            activity_date="2026-03-15",
            activity_description="March email",
        )
        ProspectActivity.objects.create(
            prospect=prospect_two,
            seller=other_seller,
            activity_type="meeting",
            activity_date="2026-04-02",
            activity_description="April meeting",
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse("dashboard:report-seller-activities"), {"month": "2026-03"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "seller-home")
        self.assertContains(response, ">2<", html=False)
        self.assertContains(response, "seller-second")
        self.assertContains(response, ">0<", html=False)
        self.assertContains(response, 'value="2026-03"')
        self.assertContains(response, 'id="seller-activity-chart"')

    def test_admin_report_defaults_to_current_month(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("dashboard:report-seller-activities"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'value="{timezone.localdate():%Y-%m}"')

    def test_admin_report_can_show_all_history(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("dashboard:report-seller-activities"), {"scope": "all"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "All history")

    def test_admin_can_create_seller_with_login_and_password(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("dashboard:seller-list"),
            {
                "username": "seller-one",
                "email": "seller-one@example.com",
                "password1": "strong-pass-123",
                "password2": "strong-pass-123",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:seller-list"))
        seller = User.objects.get(username="seller-one")
        self.assertEqual(seller.account_type, AccountType.STAFF)
        self.assertEqual(seller.email, "seller-one@example.com")
        self.assertTrue(seller.is_active)

    def test_admin_can_block_seller_access(self):
        seller = User.objects.create_user(
            username="seller-to-block",
            email="seller-to-block@example.com",
            password="strong-pass-123",
            account_type=AccountType.STAFF,
            is_active=True,
        )
        self.client.force_login(self.admin)

        response = self.client.post(reverse("dashboard:seller-toggle-access", args=[seller.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:seller-detail", args=[seller.pk]))
        seller.refresh_from_db()
        self.assertFalse(seller.is_active)

    def test_admin_can_delete_seller(self):
        seller = User.objects.create_user(
            username="seller-to-delete",
            email="seller-to-delete@example.com",
            password="strong-pass-123",
            account_type=AccountType.STAFF,
            is_active=True,
        )
        self.client.force_login(self.admin)

        response = self.client.post(reverse("dashboard:seller-delete", args=[seller.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:seller-list"))
        self.assertFalse(User.objects.filter(pk=seller.pk).exists())

    def test_non_admin_cannot_access_seller_management(self):
        self.client.force_login(self.client_user)

        response = self.client.get(reverse("dashboard:seller-list"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:home"))

    def test_sellers_are_hidden_on_client_list(self):
        seller = User.objects.create_user(
            username="seller-hidden",
            email="seller-hidden@example.com",
            password="strong-pass-123",
            account_type=AccountType.STAFF,
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse("dashboard:client-list"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, seller.email)

    def test_seller_can_link_prospect_with_registered_client(self):
        client_user = User.objects.create_user(
            username="client-linked",
            email="client-linked@example.com",
            password="strong-pass-123",
            account_type=AccountType.CLIENT,
        )
        prospect = ProspectClient.objects.create(
            seller=self.seller,
            company_name="Lead Corp",
            contact_person="Alice",
            email="alice@lead.example",
            phone="123456789",
        )
        self.client.force_login(self.seller)

        response = self.client.post(
            reverse("dashboard:prospect-link-client", args=[prospect.pk]),
            {"registered_client": client_user.pk},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:prospect-detail", args=[prospect.pk]))
        prospect.refresh_from_db()
        self.assertEqual(prospect.registered_client, client_user)

    def test_seller_cannot_link_other_seller_prospect(self):
        other_seller = User.objects.create_user(
            username="seller-other",
            email="seller-other@example.com",
            password="strong-pass-123",
            account_type=AccountType.STAFF,
        )
        client_user = User.objects.create_user(
            username="client-target",
            email="client-target@example.com",
            password="strong-pass-123",
            account_type=AccountType.CLIENT,
        )
        prospect = ProspectClient.objects.create(
            seller=other_seller,
            company_name="Foreign Lead",
            contact_person="Bob",
            email="bob@lead.example",
            phone="123456789",
        )
        self.client.force_login(self.seller)

        response = self.client.post(
            reverse("dashboard:prospect-link-client", args=[prospect.pk]),
            {"registered_client": client_user.pk},
        )

        self.assertEqual(response.status_code, 404)
        prospect.refresh_from_db()
        self.assertIsNone(prospect.registered_client)

    def test_seller_can_select_registered_client_while_creating_prospect(self):
        client_user = User.objects.create_user(
            username="client-at-create",
            email="client-at-create@example.com",
            password="strong-pass-123",
            account_type=AccountType.CLIENT,
        )
        self.client.force_login(self.seller)

        response = self.client.post(
            reverse("dashboard:prospect-create"),
            {
                "company_name": "Create Lead",
                "contact_person": "Eve",
                "email": "eve@lead.example",
                "phone": "999999999",
                "notes": "created with linked client",
                "registered_client": client_user.pk,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:seller-prospects"))

        prospect = ProspectClient.objects.get(company_name="Create Lead")
        self.assertEqual(prospect.seller, self.seller)
        self.assertEqual(prospect.registered_client, client_user)

    def test_admin_client_list_shows_linked_seller_username(self):
        client_user = User.objects.create_user(
            username="client-for-admin-list",
            email="client-for-admin-list@example.com",
            password="strong-pass-123",
            account_type=AccountType.CLIENT,
        )
        ProspectClient.objects.create(
            seller=self.seller,
            registered_client=client_user,
            company_name="Lead for Admin List",
            contact_person="Ann",
            email="ann@lead.example",
            phone="123456789",
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse("dashboard:client-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.seller.username)

    def test_seller_clients_list_shows_linked_seller_username(self):
        client_user = User.objects.create_user(
            username="client-for-seller-list",
            email="client-for-seller-list@example.com",
            password="strong-pass-123",
            account_type=AccountType.CLIENT,
        )
        ProspectClient.objects.create(
            seller=self.seller,
            registered_client=client_user,
            company_name="Lead for Seller List",
            contact_person="Tom",
            email="tom@lead.example",
            phone="123456789",
        )
        self.client.force_login(self.seller)

        response = self.client.get(reverse("dashboard:seller-clients"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.seller.username)

    def test_client_tables_show_activities_link_for_linked_client(self):
        client_user = User.objects.create_user(
            username="client-with-activity-link",
            email="client-with-activity-link@example.com",
            password="strong-pass-123",
            account_type=AccountType.CLIENT,
        )
        prospect = ProspectClient.objects.create(
            seller=self.seller,
            registered_client=client_user,
            company_name="Lead With Activity Link",
            contact_person="Lia",
            email="lia@lead.example",
            phone="123456789",
        )
        self.client.force_login(self.seller)

        seller_response = self.client.get(reverse("dashboard:seller-clients"))
        self.assertContains(seller_response, reverse("dashboard:prospect-detail", args=[prospect.pk]))

        self.client.force_login(self.admin)
        admin_response = self.client.get(reverse("dashboard:client-list"))
        self.assertContains(admin_response, reverse("dashboard:prospect-detail", args=[prospect.pk]))

    def test_admin_can_open_prospect_detail_for_linked_client(self):
        client_user = User.objects.create_user(
            username="client-admin-open",
            email="client-admin-open@example.com",
            password="strong-pass-123",
            account_type=AccountType.CLIENT,
        )
        prospect = ProspectClient.objects.create(
            seller=self.seller,
            registered_client=client_user,
            company_name="Lead Admin Open",
            contact_person="Meg",
            email="meg@lead.example",
            phone="123456789",
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse("dashboard:prospect-detail", args=[prospect.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Lead Admin Open")

    def test_admin_settlements_page_shows_only_paid_clients(self):
        paid_client = User.objects.create_user(
            username="paid-client",
            email="paid-client@example.com",
            password="strong-pass-123",
            account_type=AccountType.CLIENT,
            plan_tier=UserPlanTier.PLUS,
        )
        free_client = User.objects.create_user(
            username="free-client",
            email="free-client@example.com",
            password="strong-pass-123",
            account_type=AccountType.CLIENT,
            plan_tier=UserPlanTier.BASIC,
        )
        ProspectClient.objects.create(
            seller=self.seller,
            registered_client=paid_client,
            company_name="Paid Lead",
            contact_person="Paul",
            email="paul@lead.example",
            phone="123456789",
        )
        ProspectClient.objects.create(
            seller=self.seller,
            registered_client=free_client,
            company_name="Free Lead",
            contact_person="Frank",
            email="frank@lead.example",
            phone="123456789",
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse("dashboard:seller-settlements"), {"seller": self.seller.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, paid_client.username)
        self.assertNotContains(response, free_client.username)

    def test_admin_can_settle_paid_client_and_move_to_report(self):
        paid_client = User.objects.create_user(
            username="paid-client-settle",
            email="paid-client-settle@example.com",
            password="strong-pass-123",
            account_type=AccountType.CLIENT,
            plan_tier=UserPlanTier.PRO,
        )
        prospect = ProspectClient.objects.create(
            seller=self.seller,
            registered_client=paid_client,
            company_name="Paid Settle Lead",
            contact_person="Sara",
            email="sara@lead.example",
            phone="123456789",
        )
        self.client.force_login(self.admin)

        response = self.client.post(reverse("dashboard:seller-settlement-create", args=[prospect.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:seller-settlements"))
        settlement = SellerSettlement.objects.get(client=paid_client)
        self.assertEqual(settlement.seller, self.seller)

        page = self.client.get(reverse("dashboard:seller-settlements"), {"seller": self.seller.pk})
        self.assertNotContains(page, "Paid Settle Lead")
        self.assertContains(page, paid_client.email)
