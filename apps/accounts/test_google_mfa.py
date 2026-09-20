import time
from unittest.mock import patch
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice
from django_otp.plugins.otp_static.models import StaticToken
from .models import User, GoogleIdentity


@override_settings(GOOGLE_CLIENT_ID="test.apps.googleusercontent.com")
class GoogleSignInTests(TestCase):
    def test_google_pages_allow_identity_popup_to_return_credential(self):
        response = self.client.get(reverse("register"))

        self.assertEqual(
            response["Cross-Origin-Opener-Policy"],
            "same-origin-allow-popups",
        )
        self.assertIn(
            response["Referrer-Policy"],
            {"no-referrer-when-downgrade", "strict-origin-when-cross-origin"},
        )

    def test_real_token_signature_audience_issuer_and_expiry_validation(self):
        import json
        from types import SimpleNamespace
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        from google.auth import crypt, jwt
        from google.auth.exceptions import GoogleAuthError
        from .google_signin import verify_google_credential
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        signer = crypt.RSASigner.from_string(private, key_id="test-key")
        now = int(time.time())
        claims = {**self.claims, "aud": "test.apps.googleusercontent.com", "iss": "https://accounts.google.com", "iat": now, "exp": now + 300}
        response = SimpleNamespace(status=200, data=json.dumps({"test-key": public}).encode())
        with patch("apps.accounts.google_signin.Request") as request:
            request.return_value.return_value = response
            valid = jwt.encode(signer, claims).decode()
            self.assertEqual(verify_google_credential(valid)["sub"], self.claims["sub"])
            for change in ({"aud": "another-client"}, {"iss": "https://attacker.example"}, {"exp": now - 60}):
                with self.subTest(change=change), self.assertRaises((ValueError, GoogleAuthError)):
                    verify_google_credential(jwt.encode(signer, {**claims, **change}).decode())
            with self.assertRaises((ValueError, GoogleAuthError)):
                verify_google_credential(valid.rsplit(".", 1)[0] + "." + "A" * len(valid.rsplit(".", 1)[1]))

    def setUp(self):
        self.claims = {"sub": "google-subject", "email": "person@gmail.com", "email_verified": True, "nonce": "nonce-for-test"}

    def post(self, claims=None):
        session = self.client.session
        session["google_challenge"] = {"nonce": "nonce-for-test", "created": time.time(), "user": int(session["_auth_user_id"]) if "_auth_user_id" in session else None}
        session.save()
        with patch("apps.accounts.google_signin.verify_google_credential", return_value=claims or self.claims):
            return self.client.post(reverse("accounts:google-signin"), {"credential": "signed-google-token"})

    def test_new_google_account_is_verified_without_admin_permissions(self):
        response = self.post()
        self.assertRedirects(response, reverse("accounts:profile"), fetch_redirect_response=False)
        user = GoogleIdentity.objects.get(subject="google-subject").user
        self.assertIsNotNone(user.email_verified_at)
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_staff or user.is_superuser or user.has_usable_password())
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_google_account_must_complete_business_details_before_selecting_plan(self):
        self.post()

        page = self.client.get(reverse("accounts:profile"))
        self.assertTrue(page.context["form"].fields["company_name"].required)
        self.assertTrue(page.context["form"].fields["country"].required)
        self.assertContains(page, "Finish registration")
        response = self.client.post(
            reverse("accounts:profile"),
            {
                "username": GoogleIdentity.objects.get(subject="google-subject").user.username,
                "company_name": "Google Company",
                "email": self.claims["email"],
                "country": "Poland",
            },
        )
        self.assertRedirects(response, reverse("dashboard:plan-update"), fetch_redirect_response=False)

    def test_complete_returning_google_account_opens_dashboard(self):
        user = User.objects.create_user(
            username="complete-google",
            email=self.claims["email"],
            company_name="Complete Company",
            country="Poland",
        )
        GoogleIdentity.objects.create(user=user, subject=self.claims["sub"])

        response = self.post()

        self.assertRedirects(response, reverse("dashboard:home"), fetch_redirect_response=False)

    def test_google_only_account_cannot_use_password_change_endpoint(self):
        self.post()

        response = self.client.post(reverse("accounts:profile-password"), {})

        self.assertRedirects(response, reverse("accounts:profile"), fetch_redirect_response=False)

    def test_matching_email_requires_explicit_linking(self):
        user = User.objects.create_user(username="existing", email=self.claims["email"], password="password")
        self.post()
        self.assertFalse(GoogleIdentity.objects.exists())
        self.assertNotIn("_auth_user_id", self.client.session)
        self.client.force_login(user)
        self.post()
        self.assertEqual(GoogleIdentity.objects.get().user, user)

    def test_unverified_email_and_nonce_mismatch_are_rejected(self):
        self.post({**self.claims, "email_verified": False})
        self.post({**self.claims, "nonce": "attacker-nonce"})
        self.post({**self.claims, "email": "person@third-party.example"})
        self.assertFalse(User.objects.exists())

    def test_replay_and_csrf_are_rejected(self):
        self.post()
        self.client.logout()
        with patch("apps.accounts.google_signin.verify_google_credential") as verify:
            self.client.post(reverse("accounts:google-signin"), {"credential": "replay"})
            verify.assert_not_called()
        self.assertEqual(Client(enforce_csrf_checks=True).post(reverse("accounts:google-signin"), {"credential": "forged"}).status_code, 403)

    def test_disabled_google_account_cannot_sign_in(self):
        user = User.objects.create_user(username="disabled", email=self.claims["email"], is_active=False)
        GoogleIdentity.objects.create(user=user, subject=self.claims["sub"])
        self.post()
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(ADMIN_MFA_REQUIRED=True)
    def test_google_signin_does_not_bypass_admin_mfa(self):
        user = User.objects.create_superuser(username="admin", email=self.claims["email"], password="password")
        GoogleIdentity.objects.create(user=user, subject=self.claims["sub"])
        self.post()
        self.assertRedirects(self.client.get(reverse("dashboard:home")), reverse("accounts:mfa"), fetch_redirect_response=False)


@override_settings(ADMIN_MFA_REQUIRED=True)
class MFASetupTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username="admin", email="admin@example.com", password="test-password")
        self.client.force_login(self.user)

    def test_setup_requires_password_and_code_then_issues_recovery_codes(self):
        self.assertRedirects(self.client.get(reverse("accounts:mfa")), reverse("accounts:mfa-setup"))
        self.client.post(reverse("accounts:mfa-setup"), {"action": "start", "password": "wrong"})
        self.assertFalse(TOTPDevice.objects.exists())
        response = self.client.post(reverse("accounts:mfa-setup"), {"action": "start", "password": "test-password"})
        self.assertContains(response, "data:image/svg+xml;base64,")
        self.assertIn("no-store", response["Cache-Control"])
        device = TOTPDevice.objects.get(user=self.user)
        self.assertFalse(device.confirmed)
        code = str(totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits)).zfill(6)
        response = self.client.post(reverse("accounts:mfa-setup"), {"code": code})
        self.assertEqual(response.status_code, 200)
        device.refresh_from_db()
        self.assertTrue(device.confirmed)
        self.assertEqual(StaticToken.objects.count(), 10)
        self.assertEqual(self.client.get(reverse("dashboard:home")).status_code, 200)
        recovery = StaticToken.objects.select_related("device").first()
        self.client.logout()
        self.client.force_login(self.user)
        response = self.client.get(reverse("accounts:mfa"))
        self.assertNotContains(response, 'name="password"')
        self.assertNotContains(response, 'name="otp_challenge"')
        self.client.post(reverse("accounts:mfa"), {"otp_device": recovery.device.persistent_id, "otp_token": recovery.token})
        self.assertFalse(StaticToken.objects.filter(pk=recovery.pk).exists())
        self.assertEqual(self.client.get(reverse("dashboard:home")).status_code, 200)

    def test_existing_authenticator_cannot_be_replaced_through_setup(self):
        device = TOTPDevice.objects.create(user=self.user, confirmed=True)
        self.client.post(reverse("accounts:mfa-setup"), {"action": "start", "password": "test-password"})
        self.assertEqual(TOTPDevice.objects.count(), 1)
        self.assertEqual(TOTPDevice.objects.get().key, device.key)
