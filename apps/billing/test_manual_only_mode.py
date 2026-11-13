from html.parser import HTMLParser

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone, translation

from apps.accounts.models import User, UserPlanTier
from apps.billing.models import BillingPlanPrice, BillingProfile, ManualPlanOrder
from apps.dashboard.views import build_manual_payment_reference


class NavigationIconParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.link = None
        self.missing_icons = []
        self.link_count = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"a", "button"} and "account-nav-link" in attrs.get("class", "").split():
            self.link_count += 1
            self.link = [attrs.get("href", tag), False]
        if self.link is not None and tag == "svg":
            self.link[1] = True

    def handle_endtag(self, tag):
        if self.link is not None and tag in {"a", "button"}:
            if not self.link[1]:
                self.missing_icons.append(self.link[0])
            self.link = None


@override_settings(
    SUBSCRIPTION_BILLING_ENABLED=False,
    STRIPE_SECRET_KEY="sk_test_dormant",
    STRIPE_WEBHOOK_SECRET="whsec_dormant",
)
class ManualOnlyBillingModeTests(TestCase):
    def setUp(self):
        translation.activate("en")
        self.user = User.objects.create_user(
            username="manual-only-customer",
            email="manual-only@example.com",
            password="Strong-test-password-123!",
        )
        BillingProfile.objects.create(
            user=self.user,
            company_name="Manual Only Company",
            tax_id="PL5260250995",
            street="Prosta 1",
            postal_code="00-001",
            city="Warszawa",
            country="PL",
            invoice_email="manual-only@example.com",
        )
        for tier, amount in (
            (UserPlanTier.BASIC, 10000),
            (UserPlanTier.PLUS, 20000),
            (UserPlanTier.PRO, 40000),
        ):
            BillingPlanPrice.objects.create(
                tier=tier,
                currency="pln",
                amount=amount,
                stripe_price_id=f"price_dormant_{tier.lower()}",
            )
        self.client.force_login(self.user)

    def tearDown(self):
        translation.deactivate()

    def test_plan_selection_offers_blik_or_bank_transfer_without_subscription(self):
        response = self.client.post(
            reverse("dashboard:plan-update"),
            {"plan_tier": UserPlanTier.PLUS, "billing_currency": "pln"},
        )

        self.assertRedirects(response, reverse("dashboard:plan-payment-method"))
        methods = self.client.get(reverse("dashboard:plan-payment-method"))
        self.assertContains(methods, "BLIK")
        self.assertContains(methods, "Bank transfer")
        self.assertNotContains(methods, "subscription")
        method = self.client.post(reverse("dashboard:plan-payment-method"), {"payment_method": "manual"})
        expected_next = reverse("dashboard:plan-purchase-continue")
        self.assertRedirects(method, expected_next, fetch_redirect_response=False)

        continuation = self.client.get(expected_next)
        self.assertRedirects(continuation, reverse("dashboard:plan-purchase-terms"))
        terms = self.client.get(reverse("dashboard:plan-purchase-terms"))
        self.assertContains(terms, "bank transfer")
        self.assertNotContains(terms, "Stripe")
        self.assertNotContains(terms, "subscription")

        acceptance = self.client.post(
            reverse("dashboard:plan-purchase-terms"),
            {"terms_accepted": "on"},
        )
        self.assertRedirects(acceptance, reverse("dashboard:manual-plan-confirm"))
        confirmation = self.client.get(reverse("dashboard:manual-plan-confirm"))
        self.assertContains(confirmation, "Confirm annual plan order")
        self.assertNotContains(confirmation, "Stripe")
        self.assertNotContains(confirmation, "subscription")

    @override_settings(
        MANUAL_PAYMENT_RECIPIENT="Wicky Wave Sp. z o.o.",
        MANUAL_PAYMENT_ADDRESS="ul. Ezopa 57, 04-805 Warszawa",
        MANUAL_PAYMENT_TAX_ID="9522290935",
        MANUAL_PAYMENT_IBAN_PLN="PL79 1160 2202 0000 0007 3928 4688",
        MANUAL_PAYMENT_BANK="",
    )
    def test_transfer_details_and_reference_survive_purchase(self):
        self.user.billing_profile.company_name = "Żółć Łąka Sp. z o.o."
        self.user.billing_profile.save(update_fields=["company_name"])
        session = self.client.session
        session["pending_plan_purchase"] = {
            "tier": UserPlanTier.PLUS, "currency": "pln", "payment_method": "manual", "terms_accepted": True,
        }
        session.save()

        confirmation = self.client.get(reverse("dashboard:manual-plan-confirm"))
        reference = self.client.session["pending_plan_purchase"]["payment_reference"]
        self.assertRegex(reference, rf"^{timezone.localdate():%Y%m%d}-ZOLC-LAKA-SP-Z-OO-PLUS-[0-9A-F]{{8}}$")
        self.assertContains(confirmation, reference)
        for value in ("Wicky Wave Sp. z o.o.", "ul. Ezopa 57, 04-805 Warszawa", "9522290935", "PL79 1160 2202 0000 0007 3928 4688"):
            self.assertContains(confirmation, value)
        self.assertNotContains(confirmation, "Demo Bank")

        response = self.client.post(reverse("dashboard:manual-plan-confirm"))
        self.assertRedirects(response, reverse("dashboard:home"))
        self.assertEqual(ManualPlanOrder.objects.get(user=self.user).payment_reference, reference)
        portal = self.client.get(reverse("dashboard:billing-portal"))
        for value in (reference, "Wicky Wave Sp. z o.o.", "ul. Ezopa 57, 04-805 Warszawa", "9522290935", "PL79 1160 2202 0000 0007 3928 4688"):
            self.assertContains(portal, value)

    def test_reference_is_unique_and_has_fallback_for_non_latin_name(self):
        self.user.billing_profile.company_name = "東京"
        self.user.billing_profile.save(update_fields=["company_name"])
        first = build_manual_payment_reference(self.user, UserPlanTier.BASIC)
        second = build_manual_payment_reference(self.user, UserPlanTier.BASIC)
        self.assertRegex(first, rf"^{timezone.localdate():%Y%m%d}-KLIENT-BASIC-[0-9A-F]{{8}}$")
        self.assertNotEqual(first, second)

    @override_settings(INTERNATIONAL_BILLING_ENABLED=True, MANUAL_PAYMENT_IBAN_EUR="")
    def test_eur_transfer_requires_configured_account(self):
        profile = self.user.billing_profile
        profile.country = "DE"
        profile.save(update_fields=["country"])
        session = self.client.session
        session["pending_plan_purchase"] = {
            "tier": UserPlanTier.PLUS, "currency": "eur", "payment_method": "manual", "terms_accepted": True,
        }
        session.save()

        for method in (self.client.get, self.client.post):
            response = method(reverse("dashboard:manual-plan-confirm"))
            self.assertRedirects(response, reverse("dashboard:plan-payment-method"), fetch_redirect_response=False)
        self.assertFalse(ManualPlanOrder.objects.filter(user=self.user).exists())

    def test_navigation_links_have_icons_for_customers_admins_and_guests(self):
        admin = User.objects.create_superuser("nav-admin", "nav-admin@example.com", "Admin-test-password-123!")
        for user in (self.user, admin, None):
            with self.subTest(user=user):
                if user:
                    self.client.force_login(user)
                else:
                    self.client.logout()
                for route in ("landing", "dashboard:home" if user else "login"):
                    response = self.client.get(reverse(route))
                    parser = NavigationIconParser()
                    parser.feed(response.content.decode())
                    self.assertGreater(parser.link_count, 0)
                    self.assertEqual(parser.missing_icons, [])

    def test_subscription_routes_are_hidden_but_local_price_editor_is_available(self):
        session = self.client.session
        session["pending_plan_purchase"] = {"tier": "BASIC", "currency": "pln"}
        session.save()
        self.assertEqual(self.client.get(reverse("dashboard:plan-payment-method")).status_code, 200)
        self.assertRedirects(
            self.client.get(reverse("dashboard:stripe-plan-confirm")),
            reverse("dashboard:plan-update"),
        )
        self.assertEqual(
            self.client.post(reverse("stripe-webhook"), data=b"{}", content_type="application/json").status_code,
            400,
        )

        admin = User.objects.create_superuser(
            username="manual-only-admin",
            email="manual-only-admin@example.com",
            password="Admin-test-password-123!",
        )
        self.client.force_login(admin)
        self.assertEqual(self.client.get(reverse("dashboard:billing-price-management")).status_code, 200)

    def test_published_manual_pages_do_not_render_subscription_copy(self):
        plan_page = self.client.get(reverse("dashboard:plan-update"))
        self.assertContains(plan_page, "pay with BLIK or by bank transfer")
        self.assertNotContains(plan_page, "Stripe")
        self.assertNotContains(plan_page, "subscription")

        admin = User.objects.create_superuser(
            username="manual-copy-admin",
            email="manual-copy-admin@example.com",
            password="Admin-test-password-123!",
        )
        self.client.force_login(admin)
        home = self.client.get(reverse("dashboard:home"))
        self.assertContains(home, "Turnover")
        self.assertNotContains(home, "Subscriptions & Turnovers")
        self.assertContains(home, "Price management")

        overview = self.client.get(reverse("dashboard:billing-overview"))
        self.assertContains(overview, "Annual Plans & Turnover")
        self.assertNotContains(overview, "Stripe subscription")
        self.assertNotContains(overview, "No subscriptions yet")

        notifications = self.client.get(reverse("dashboard:notifications"))
        self.assertNotContains(notifications, '<option value="stripe">', html=False)
