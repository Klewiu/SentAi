from django.conf import settings
from django.core import signing
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse


def send_verification(user, language_code=None):
    email = user.pending_email or user.email
    token = signing.dumps({"user": user.pk, "email": email, "password": user.get_session_auth_hash()}, salt="email-verification", compress=True)
    url = settings.SITE_BASE_URL + reverse("accounts:verify-email", args=[token])
    language_code = language_code or settings.LANGUAGE_CODE
    language_code = "pl" if language_code.startswith("pl") else "en"
    context = {"verification_url": url}
    subject = (
        "Potwierdź swój adres e-mail"
        if language_code == "pl"
        else "Confirm your email address"
    )
    text_body = render_to_string(
        "emails/verification_email.txt",
        {**context, "language_code": language_code},
    )
    html_body = render_to_string(
        "emails/verification_email.html",
        {**context, "language_code": language_code},
    )

    message = EmailMultiAlternatives(
        subject,
        text_body,
        settings.DEFAULT_FROM_EMAIL,
        [email],
    )
    message.attach_alternative(html_body, "text/html")

    message.send()
