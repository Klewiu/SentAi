import re
from django.core import mail
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from rest_framework.authtoken.models import Token
from .models import User
from .forms import ProfilePasswordChangeForm


class AccountSecurityTests(TestCase):
    @override_settings(DEFAULT_FROM_EMAIL="kontakt@xoaila.pl")
    def test_verification_email_uses_configured_sender(self):
        from .verification import send_verification

        send_verification(self.user, "pl")

        self.assertEqual(mail.outbox[-1].from_email, "kontakt@xoaila.pl")
        self.assertEqual(mail.outbox[-1].to, ["audit@example.com"])
        self.assertEqual(mail.outbox[-1].subject, "Potwierdź swój adres e-mail")
        self.assertIn("Potwierdź adres e-mail", mail.outbox[-1].body)
        html_body = mail.outbox[-1].alternatives[0][0]
        self.assertIn(">xoaila</span>", html_body)
        self.assertIn("background:#5ca197", html_body)
        self.assertNotIn("cid:xoaila-logo", html_body)

    def test_verification_email_supports_english(self):
        from .verification import send_verification

        send_verification(self.user, "en")

        self.assertEqual(mail.outbox[-1].subject, "Confirm your email address")
        self.assertIn("Confirm your email address", mail.outbox[-1].body)
        self.assertIn(">xoaila</span>", mail.outbox[-1].alternatives[0][0])

    def test_failed_login_offers_password_reset_and_email_link_changes_password(self):
        from django.utils.translation import override

        with override("pl"):
            failed_login = self.client.post(
                reverse("login"),
                {"username": self.user.username, "password": "incorrect-password"},
            )
            self.assertContains(failed_login, "Zresetuj hasło")
            self.assertEqual(len(mail.outbox), 0)

            reset_response = self.client.post(
                reverse("password_reset"),
                {"email": self.user.username},
            )

        self.assertEqual(reset_response.status_code, 302)
        self.assertTrue(reset_response.url.endswith("/accounts/password_reset/done/"))
        self.assertEqual(len(mail.outbox), 1)
        reset_email = mail.outbox[0]
        self.assertEqual(reset_email.to, [self.user.email])
        self.assertEqual(reset_email.subject, "Reset hasła do konta Xoaila")
        self.assertIn("zresetowanie", reset_email.body)
        self.assertIn(">Zresetuj", reset_email.alternatives[0][0])

        reset_path = re.search(
            r"https?://testserver(?P<path>/(?:pl/)?accounts/reset/[^\s]+)",
            reset_email.body,
        ).group("path")
        confirm_response = self.client.get(reset_path)
        self.assertEqual(confirm_response.status_code, 302)
        confirm_path = confirm_response.url
        confirm_page = self.client.get(confirm_path)
        self.assertContains(confirm_page, "Ustaw nowe hasło")

        complete_response = self.client.post(
            confirm_path,
            {
                "new_password1": "Replacement-password-456",
                "new_password2": "Replacement-password-456",
            },
        )
        self.assertEqual(complete_response.status_code, 302)
        self.assertTrue(complete_response.url.endswith("/accounts/reset/done/"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Replacement-password-456"))

    def test_password_reset_does_not_disclose_unknown_accounts(self):
        response = self.client.post(
            reverse("password_reset"),
            {"email": "unknown-account"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.endswith("/accounts/password_reset/done/"))
        self.assertEqual(len(mail.outbox), 0)

    def test_password_reset_accepts_case_insensitive_email(self):
        response = self.client.post(
            reverse("password_reset"),
            {"email": "AUDIT@EXAMPLE.COM"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])

    def test_disabled_account_cannot_be_reactivated_by_email_change(self):
        from .verification import send_verification
        self.user.pending_email = "replacement@example.com"
        self.user.save()
        send_verification(self.user)
        url = re.search(r"https?://\S+", mail.outbox[-1].body).group()
        self.user.is_active = False
        self.user.save()
        self.client.get(url)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertEqual(self.user.email, "audit@example.com")

    @override_settings(ADMIN_MFA_REQUIRED=True)
    def test_admin_can_sign_in_with_valid_otp(self):
        from django_otp.plugins.otp_totp.models import TOTPDevice
        from django_otp.oath import totp
        self.user.is_staff = self.user.is_superuser = True
        self.user.save()
        device = TOTPDevice.objects.create(user=self.user, name="test", confirmed=True)
        token = totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits)
        response = self.client.post(reverse("accounts:mfa"), {"username": "AUDIT@example.com", "password": "Original-password-123", "otp_device": device.persistent_id, "otp_token": str(token).zfill(device.digits)})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(reverse("dashboard:home")).status_code, 200)

    def setUp(self):
        self.user = User.objects.create_user(username="audit", email="audit@example.com", password="Original-password-123")

    def test_api_session_login_requires_csrf(self):
        response = Client(enforce_csrf_checks=True).post("/api/auth/login/", {"login": "audit", "password": "Original-password-123"})
        self.assertEqual(response.status_code, 403)

    def test_password_change_revokes_existing_api_token(self):
        token = Token.objects.create(user=self.user)
        form = ProfilePasswordChangeForm(user=self.user, data={"old_password": "Original-password-123", "new_password1": "Replacement-password-456", "new_password2": "Replacement-password-456"})
        self.assertTrue(form.is_valid())
        form.save()
        self.assertFalse(Token.objects.filter(pk=token.pk).exists())

    @override_settings(REGISTRATION_EMAIL_VERIFICATION_REQUIRED=True)
    def test_registration_requires_email_confirmation_and_rejects_replay(self):
        self.client.post(reverse("register"), {"username": "new", "company_name": "New Company", "country": "Poland", "email": "new@example.com", "password1": "Original-password-123", "password2": "Original-password-123"})
        user = User.objects.get(username="new")
        self.assertFalse(user.is_active)
        self.assertFalse(self.client.login(username="new", password="Original-password-123"))
        url = re.search(r"https?://\S+", mail.outbox[-1].body).group()
        self.client.get(url)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        user.is_active = False
        user.save()
        self.client.get(url)
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    @override_settings(AUTH_RATE_LIMIT=2)
    def test_login_throttles_repeated_attempts(self):
        for _ in range(2):
            self.client.post("/api/auth/login/", {"login": "audit", "password": "wrong"})
        self.assertEqual(self.client.post("/api/auth/login/", {"login": "audit", "password": "wrong"}).status_code, 429)

    @override_settings(ADMIN_MFA_REQUIRED=True)
    def test_admin_dashboard_and_api_require_mfa(self):
        self.user.is_superuser = self.user.is_staff = True
        self.user.save()
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get(reverse("dashboard:home")), reverse("accounts:mfa"), fetch_redirect_response=False)
        token = Token.objects.create(user=self.user)
        client = Client()
        self.assertIn(client.get("/api/auth/me/", HTTP_AUTHORIZATION="Token " + token.key).status_code, (401, 403))
