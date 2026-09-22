from django.conf import settings
from django.core import signing
from django.core.mail import send_mail
from django.urls import reverse


def send_verification(user):
    email = user.pending_email or user.email
    token = signing.dumps({"user": user.pk, "email": email, "password": user.get_session_auth_hash()}, salt="email-verification", compress=True)
    url = settings.SITE_BASE_URL + reverse("accounts:verify-email", args=[token])
    send_mail("Confirm your email address", f"Confirm your email address using this link (valid for 24 hours):\n{url}", settings.DEFAULT_FROM_EMAIL, [email])
