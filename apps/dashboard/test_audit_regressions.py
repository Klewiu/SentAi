from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import AccountType, User
from apps.dashboard.forms import ProspectClientForm, ProspectLinkClientForm
from apps.companies.models import Organization, VerificationStatus
from apps.notifications.models import AdminNotification, CustomerNotification
from apps.notifications.services import scan_admin_notifications
from apps.sales.models import ProspectClient


class DashboardAuditRegressionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="audit-admin",
            email="audit-admin@example.com",
            password="strong-pass-123",
        )
        self.seller = User.objects.create_user(
            username="active-seller",
            email="active-seller@example.com",
            password="strong-pass-123",
            account_type=AccountType.STAFF,
        )
        self.closed_client = User.objects.create_user(
            username="closed-client",
            email="closed-client@example.com",
            account_type=AccountType.CLIENT,
            is_active=False,
            closed_display_name="Archived Company",
        )

    def test_closed_client_is_not_offered_for_new_prospect_link(self):
        self.assertNotIn(
            self.closed_client,
            ProspectClientForm().fields["registered_client"].queryset,
        )
        self.assertNotIn(
            self.closed_client,
            ProspectLinkClientForm().fields["registered_client"].queryset,
        )

    def test_current_closed_link_remains_visible_for_audit(self):
        prospect = ProspectClient.objects.create(
            seller=self.seller,
            registered_client=self.closed_client,
            company_name="Archived Company",
            contact_person="Former customer",
            email="former@example.com",
            phone="",
        )

        queryset = ProspectLinkClientForm(prospect=prospect).fields[
            "registered_client"
        ].queryset

        self.assertIn(self.closed_client, queryset)

    def test_admin_can_search_closed_client_by_archived_name(self):
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse("dashboard:client-list"), {"q": "Archived Company"}
        )

        self.assertContains(response, "Archived Company")

    def test_inactive_seller_is_not_available_for_assignment(self):
        inactive_seller = User.objects.create_user(
            username="inactive-seller",
            email="inactive-seller@example.com",
            account_type=AccountType.STAFF,
            is_active=False,
        )
        active_client = User.objects.create_user(
            username="active-client",
            email="active-client@example.com",
            account_type=AccountType.CLIENT,
        )
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse("dashboard:client-detail", args=[active_client.pk])
        )

        self.assertNotIn(inactive_seller, response.context["available_sellers"])

    def test_company_verification_notification_opens_and_resolves(self):
        active_client = User.objects.create_user(
            username="verification-client",
            email="verification-client@example.com",
            account_type=AccountType.CLIENT,
        )
        organization = Organization.objects.create(
            owner=active_client,
            name="Company awaiting verification",
            slug="company-awaiting-verification",
        )
        reference = f"organization:{organization.pk}:verification-needed"
        notice = AdminNotification.objects.get(reference_key=reference)
        self.assertIsNone(notice.closed_at)

        organization.verification_status = VerificationStatus.HUMAN_ADMIN_VERIFIED
        organization.save(update_fields=["verification_status", "updated_at"])

        notice.refresh_from_db()
        self.assertIsNotNone(notice.closed_at)
        self.assertIsNotNone(notice.resolved_at)

    def test_notification_scan_backfills_unverified_company(self):
        active_client = User.objects.create_user(
            username="backfill-client",
            email="backfill-client@example.com",
            account_type=AccountType.CLIENT,
        )
        organization = Organization.objects.create(
            owner=active_client,
            name="Backfill Company",
            slug="backfill-company",
        )
        reference = f"organization:{organization.pk}:verification-needed"
        AdminNotification.objects.filter(reference_key=reference).delete()

        scan_admin_notifications()

        self.assertTrue(
            AdminNotification.objects.filter(
                reference_key=reference,
                closed_at__isnull=True,
            ).exists()
        )

    def test_closed_customer_does_not_receive_new_invoice_notification(self):
        from apps.billing.models import BillingInvoice

        invoice = BillingInvoice.objects.create(
            user=self.closed_client,
            invoice_number="CLOSED-CUSTOMER-INVOICE",
            issued_at="2026-09-20",
            document="invoices/closed-customer.pdf",
        )

        self.assertFalse(
            CustomerNotification.objects.filter(
                user=self.closed_client,
                reference_key=f"customer:{self.closed_client.pk}:invoice:{invoice.pk}",
            ).exists()
        )
