import re
from django.core import mail
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from rest_framework.authtoken.models import Token
from .models import User
from .forms import ProfilePasswordChangeForm


class AccountSecurityTests(TestCase):
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
        response = self.client.post(reverse("accounts:mfa"), {"username": self.user.username, "password": "Original-password-123", "otp_device": device.persistent_id, "otp_token": str(token).zfill(device.digits)})
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
