import logging
import secrets
import time
import uuid

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.shortcuts import redirect
from django.utils import timezone
from django.views import View
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.id_token import verify_oauth2_token

from .models import GoogleIdentity, User


logger = logging.getLogger(__name__)


def google_context(request):
    enabled = bool(settings.GOOGLE_CLIENT_ID)
    nonce = ""
    if enabled and request.method == "GET" and any(part in request.path for part in ("/login/", "/register/", "/profile/")):
        challenge = request.session.get("google_challenge", {})
        current_id = request.user.pk if request.user.is_authenticated else None
        if time.time() - challenge.get("created", 0) > 600 or challenge.get("user") != current_id:
            challenge = {"nonce": secrets.token_urlsafe(32), "created": time.time(), "user": request.user.pk if request.user.is_authenticated else None}
            request.session["google_challenge"] = challenge
        nonce = challenge["nonce"]
    return {"google_signin_enabled": enabled, "google_client_id": settings.GOOGLE_CLIENT_ID, "google_nonce": nonce}


def verify_google_credential(credential):
    # Google's maintained library validates signature, audience, issuer and expiry.
    transport = Request()
    def fetch(url, method="GET", **kwargs):
        kwargs.pop("timeout", None)
        return transport(url=url, method=method, timeout=10, **kwargs)
    try:
        return verify_oauth2_token(credential, fetch, settings.GOOGLE_CLIENT_ID)
    except ValueError:
        raise ValueError("Google sign-in could not be verified. Please try again.") from None


@transaction.atomic
def resolve_google_user(claims, current_user=None):
    identity = GoogleIdentity.objects.select_related("user").filter(subject=claims["sub"]).first()
    if identity:
        if current_user and identity.user_id != current_user.pk:
            raise ValueError("This Google account is already linked to another profile.")
        if not identity.user.is_active:
            raise ValueError("This account is disabled. Contact support.")
        return identity.user
    email = claims["email"].strip().lower()
    if current_user:
        user = User.objects.select_for_update().get(pk=current_user.pk)
        if not user.is_active or user.email.lower() != email:
            raise ValueError("Choose the Google account with the same email as your profile.")
        if GoogleIdentity.objects.filter(user=user).exists():
            raise ValueError("A different Google account is already linked.")
    else:
        if User.objects.filter(email__iexact=email).exists():
            raise ValueError("An account already uses this email. Sign in with your password, then link Google from your profile.")
        user = User.objects.create_user(username="google_" + uuid.uuid4().hex[:20], email=email, email_verified_at=timezone.now(), first_name=str(claims.get("given_name", ""))[:150], last_name=str(claims.get("family_name", ""))[:150])
    GoogleIdentity.objects.create(user=user, subject=claims["sub"])
    if not user.email_verified_at:
        user.email_verified_at = timezone.now()
        user.save(update_fields=["email_verified_at"])
    return user


class GoogleSignInView(View):
    def post(self, request):
        destination = "accounts:profile" if request.user.is_authenticated else "login"
        challenge = request.session.pop("google_challenge", {})
        try:
            if not settings.GOOGLE_CLIENT_ID or not challenge or time.time() - challenge.get("created", 0) > 600:
                raise ValueError("Please reload the sign-in page and try again.")
            current_id = request.user.pk if request.user.is_authenticated else None
            if challenge.get("user") != current_id:
                raise ValueError("Your session changed. Please reload this page.")
            credential = request.POST.get("credential", "")
            if not credential or len(credential) > 16384:
                raise ValueError("Google sign-in could not be verified.")
            claims = verify_google_credential(credential)
            if not isinstance(claims.get("nonce"), str) or not secrets.compare_digest(claims["nonce"], challenge.get("nonce", "")):
                raise ValueError("Google sign-in could not be verified. Please try again.")
            email = claims.get("email", "")
            validate_email(email)
            if claims.get("email_verified") is not True or not (email.lower().endswith("@gmail.com") or claims.get("hd")):
                raise ValueError("Use a Gmail or Google Workspace account, or sign in with your password.")
            if not isinstance(claims.get("sub"), str) or not 1 <= len(claims["sub"]) <= 255:
                raise ValueError("Google sign-in could not be verified.")
            current = request.user if request.user.is_authenticated else None
            user = resolve_google_user(claims, current)
            if current:
                messages.success(request, "Google is linked. You can use it to sign in next time.")
                return redirect("accounts:profile")
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            # This proves the Google identity, not an administrator's second factor.
            if not user.company_name.strip() or not user.country.strip():
                messages.info(
                    request,
                    "Uzupełnij nazwę firmy i kraj, aby zakończyć rejestrację."
                    if request.LANGUAGE_CODE == "pl"
                    else "Complete your company name and country to finish registration.",
                )
                return redirect("accounts:profile")
            return redirect("dashboard:home")
        except ValueError as error:
            messages.error(request, str(error))
        except (GoogleAuthError, ValidationError, IntegrityError, KeyError, TypeError):
            # Never log the credential. The exception type and traceback are
            # sufficient to diagnose provider/network/database failures.
            logger.exception("Google sign-in callback failed")
            messages.error(request, "Google sign-in could not be completed. Please try again or use your password.")
        return redirect(destination)
