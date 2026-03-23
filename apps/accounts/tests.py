from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


User = get_user_model()


class RegistrationFlowTests(TestCase):
    def test_register_creates_user_without_email_verification(self):
        response = self.client.post(
            reverse("register"),
            {
                "username": "newclient",
                "company_name": "Acme Sp. z o.o.",
                "email": "client@example.com",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )

        self.assertRedirects(response, reverse("login"))
        user = User.objects.get(username="newclient")
        self.assertEqual(user.company_name, "Acme Sp. z o.o.")
        self.assertEqual(user.email, "client@example.com")
        self.assertIsNotNone(user.date_joined)

    def test_last_login_is_updated_after_first_sign_in(self):
        User.objects.create_user(
            username="newclient",
            email="client@example.com",
            company_name="Acme Sp. z o.o.",
            password="StrongPass123!",
        )

        response = self.client.post(
            reverse("login"),
            {
                "username": "newclient",
                "password": "StrongPass123!",
            },
        )

        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username="newclient")
        self.assertIsNotNone(user.last_login)
