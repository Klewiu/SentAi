import logging
import uuid
from django.db import transaction
from django.contrib import messages
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect

logger = logging.getLogger(__name__)
import stripe
from django.conf import settings
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import FormView, TemplateView
from rest_framework import permissions, status
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema

from .forms import ProfileForm, ProfilePasswordChangeForm, UserRegistrationForm
from .serializers import CurrentUserSerializer, TokenLoginSerializer


@transaction.atomic
def soft_close_user_with_financial_records(user):
    """Revoke access and personal profile data while retaining accounting rows."""
    from .models import GoogleIdentity, User

    user = User.objects.select_for_update().get(pk=user.pk)
    from apps.notifications.models import CustomerNotification

    now = timezone.now()
    CustomerNotification.objects.filter(user=user, closed_at__isnull=True).update(
        resolved_at=now,
        closed_at=now,
        updated_at=now,
    )
    user.organizations.all().delete()
    GoogleIdentity.objects.filter(user=user).delete()
    anonymized = f"{user.pk}-{uuid.uuid4().hex}"
    user.is_active = False
    user.set_unusable_password()
    user.closed_display_name = (
        user.company_name or user.get_full_name() or user.username
    )[:255]
    user.username = f"closed_{anonymized}"[:150]
    user.email = f"closed-{anonymized}@deleted.invalid"
    user.first_name = ""
    user.last_name = ""
    user.company_name = ""
    user.country = ""
    user.pending_email = ""
    user.email_verified_at = None
    user.closed_at = now
    user.save()
    return user


class RegisterView(FormView):
    template_name = "registration/register.html"
    form_class = UserRegistrationForm
    success_url = reverse_lazy("accounts:verify-notice")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("dashboard:home")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["language_code"] = self.request.LANGUAGE_CODE
        return kwargs

    def form_valid(self, form):
        user = form.save(commit=False)
        if not settings.REGISTRATION_EMAIL_VERIFICATION_REQUIRED:
            user.is_active = True
            user.registration_pending = False
            user.save()
            messages.success(
                self.request,
                "Konto zostało utworzone. Możesz się zalogować."
                if self.request.LANGUAGE_CODE == "pl"
                else "Account created. You can now sign in.",
            )
            return redirect("login")
        user.is_active = False
        user.registration_pending = True
        user.save()
        from .verification import send_verification
        try:
            send_verification(user)
        except Exception:
            logger.exception("Registration verification delivery failed")
            messages.warning(self.request, "Email could not be delivered. Please request another confirmation link.")
        return super().form_valid(form)


@method_decorator(csrf_protect, name="dispatch")
class TokenLoginView(APIView):
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    @extend_schema(request=TokenLoginSerializer, responses=OpenApiTypes.OBJECT)
    def post(self, request, *args, **kwargs):
        serializer = TokenLoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        if settings.ADMIN_MFA_REQUIRED and (user.is_superuser or user.is_staff):
            return Response({"detail": "Use administrator two-factor sign-in."}, status=403)
        token, _ = Token.objects.get_or_create(user=user)
        login(request, user)
        return Response(
            {
                "token": token.key,
                "user": CurrentUserSerializer(user).data,
            },
            status=status.HTTP_200_OK,
        )


@extend_schema(exclude=True)
class CurrentUserView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        return Response(CurrentUserSerializer(request.user).data)


class ProfileView(LoginRequiredMixin, FormView):
    template_name = "registration/profile.html"
    form_class = ProfileForm
    success_url = reverse_lazy("accounts:profile")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.request.user
        kwargs["language_code"] = self.request.LANGUAGE_CODE
        self.profile_completion_required = self._requires_business_details()
        kwargs["require_business_details"] = self.profile_completion_required
        return kwargs

    def _requires_business_details(self):
        return (
            hasattr(self.request.user, "google_identity")
            and (not self.request.user.company_name.strip() or not self.request.user.country.strip())
        )

    def form_valid(self, form):
        from .models import User
        old_email = User.objects.get(pk=self.request.user.pk).email
        user = form.save(commit=False)
        if user.email != old_email:
            user.pending_email = user.email
            user.email = old_email
            user.save()
            from .verification import send_verification
            try:
                send_verification(user)
                messages.info(self.request, "Confirm your new email address before it replaces your current address.")
            except Exception:
                logger.exception("Email change verification delivery failed")
                messages.warning(self.request, "Please request another email confirmation link.")
        else:
            user.save()
            messages.success(self.request, "Profile updated successfully.")
        if getattr(self, "profile_completion_required", False):
            return redirect("dashboard:plan-update")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if "password_form" not in context:
            context["password_form"] = ProfilePasswordChangeForm(
                user=self.request.user,
                language_code=self.request.LANGUAGE_CODE,
            )
        context["profile_completion_required"] = self._requires_business_details()
        return context


class ProfilePasswordChangeView(LoginRequiredMixin, FormView):
    template_name = "registration/profile.html"
    form_class = ProfilePasswordChangeForm
    success_url = reverse_lazy("accounts:profile")

    def dispatch(self, request, *args, **kwargs):
        if not request.user.has_usable_password():
            messages.error(
                request,
                "To konto korzysta z logowania Google." if request.LANGUAGE_CODE == "pl" else "This account uses Google sign-in.",
            )
            return redirect("accounts:profile")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["language_code"] = self.request.LANGUAGE_CODE
        return kwargs

    def form_valid(self, form):
        form.save()
        update_session_auth_hash(self.request, form.user)
        if self.request.LANGUAGE_CODE == "pl":
            messages.success(self.request, "Hasło zostało zmienione.")
        else:
            messages.success(self.request, "Password changed successfully.")
        return super().form_valid(form)

    def form_invalid(self, form):
        return self.render_to_response({"form": ProfileForm(instance=self.request.user), "password_form": form})


class AccountCloseView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        user = request.user
        billing_subscription = getattr(user, "billing_subscription", None)
        subscription_id = getattr(billing_subscription, "stripe_subscription_id", "") if billing_subscription else ""
        has_active_subscription = (
            billing_subscription
            and subscription_id
            and billing_subscription.status not in {"canceled", "incomplete_expired"}
        )

        if has_active_subscription:
            if not settings.STRIPE_SECRET_KEY:
                if request.LANGUAGE_CODE == "pl":
                    messages.error(request, "Nie mozna zamknac konta, bo Stripe nie jest skonfigurowany do anulowania subskrypcji.")
                else:
                    messages.error(request, "Account cannot be closed because Stripe is not configured to cancel the subscription.")
                return redirect("accounts:profile")

            stripe.api_key = settings.STRIPE_SECRET_KEY
            try:
                stripe.Subscription.delete(subscription_id)
            except Exception:
                # Stripe may finish cancellation even when the response is lost.
                # Confirm the remote state before deciding that account closure
                # failed, which also makes a repeated closure request idempotent.
                logger.warning(
                    "Stripe subscription cancellation raised; checking remote state",
                    exc_info=True,
                )
                try:
                    remote_subscription = stripe.Subscription.retrieve(subscription_id)
                    remote_status = getattr(remote_subscription, "status", "")
                    remote_customer = getattr(remote_subscription, "customer", "")
                    if isinstance(remote_subscription, dict):
                        remote_status = remote_subscription.get("status", remote_status)
                        remote_customer = remote_subscription.get("customer", remote_customer)
                    if remote_status != "canceled" or (
                        billing_subscription.stripe_customer_id
                        and remote_customer != billing_subscription.stripe_customer_id
                    ):
                        raise ValueError("Stripe subscription is still active or belongs to another customer")
                except Exception:
                    logger.exception("Could not confirm Stripe subscription cancellation")
                    if request.LANGUAGE_CODE == "pl":
                        messages.error(request, "Nie udalo sie anulowac subskrypcji Stripe. Konto nie zostalo zamkniete.")
                    else:
                        messages.error(request, "Could not cancel the Stripe subscription. Account was not closed.")
                    return redirect("accounts:profile")

            billing_subscription.status = "canceled"
            billing_subscription.cancel_at_period_end = False
            billing_subscription.save(update_fields=["status", "cancel_at_period_end", "updated_at"])

        logout(request)
        # Keep an auditable closed-customer row for administrators while
        # removing public/customer identity data and freeing the original email.
        soft_close_user_with_financial_records(user)
        if request.LANGUAGE_CODE == "pl":
            messages.success(request, "Konto zostalo zamkniete. Dane zostaly usuniete, a subskrypcja anulowana.")
        else:
            messages.success(request, "Your account has been closed and public profiles removed. Financial records are retained for accounting.")
        return redirect("login")


class VerificationNoticeView(TemplateView):
    template_name = "registration/verify_notice.html"


class VerifyEmailView(View):
    @transaction.atomic
    def get(self, request, token):
        from django.core import signing
        from django.utils import timezone
        from django.db import IntegrityError
        from .models import User
        try:
            data = signing.loads(token, salt="email-verification", max_age=86400)
            user = User.objects.select_for_update().get(pk=data["user"])
            if data["password"] != user.get_session_auth_hash() or data["email"] != (user.pending_email or user.email):
                raise ValueError("Invalid confirmation")
            if user.email_verified_at and not user.pending_email:
                raise ValueError("Already confirmed")
            if (user.pending_email and not user.is_active) or (not user.pending_email and not user.registration_pending):
                raise ValueError("Account is disabled")
            if user.pending_email:
                if User.objects.filter(email__iexact=user.pending_email).exclude(pk=user.pk).exists():
                    raise ValueError("Address already in use")
                user.email = user.pending_email
                user.pending_email = ""
            user.email_verified_at = timezone.now()
            user.registration_pending = False
            user.is_active = True
            user.save()
            messages.success(request, "Email confirmed. You can sign in.")
            return redirect("login")
        except (signing.BadSignature, User.DoesNotExist, KeyError, ValueError, IntegrityError):
            messages.error(request, "This confirmation link is invalid or expired. Request another link.")
            return redirect("accounts:verify-notice")


class ResendVerificationView(View):
    def post(self, request):
        from .models import User
        from django.db.models import Q
        from .verification import send_verification
        email = request.POST.get("email", "").strip().lower()
        user = User.objects.filter(Q(email__iexact=email, is_active=False, registration_pending=True) | Q(pending_email__iexact=email, is_active=True)).first() if email else None
        if user:
            try:
                send_verification(user)
            except Exception:
                logger.exception("Verification resend failed")
        messages.info(request, "If this address needs confirmation, we have sent a link.")
        return redirect("accounts:verify-notice")


from django.contrib.auth.views import LoginView
from django_otp.forms import OTPAuthenticationForm


class MFALoginView(LoginView):
    authentication_form = OTPAuthenticationForm
    template_name = "registration/mfa_login.html"

    def form_valid(self, form):
        import django_otp
        response = super().form_valid(form)
        django_otp.login(self.request, form.get_user().otp_device)
        return response
