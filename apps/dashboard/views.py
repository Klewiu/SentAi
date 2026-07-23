from datetime import date, timedelta
import uuid

import stripe
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import CreateView, FormView, TemplateView, UpdateView
from django.db import models
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from apps.accounts.models import AccountType, User, UserPlanTier
from apps.billing.models import (
    BillingInvoice,
    BillingPayment,
    BillingPlanPrice,
    BillingProfile,
    BillingSubscription,
    ManualPlanOrder,
    ManualPlanOrderStatus,
)
from apps.billing.services import (
    activate_paid_plan,
    downgrade_to_basic,
    format_amount,
    get_active_plan_price,
    normalize_billing_currency,
    plan_price_label,
    record_invoice_payment,
    sync_subscription_from_stripe,
    supported_billing_currencies,
)
from apps.companies.forms import OrganizationForm
from apps.companies.models import Organization, VerificationStatus
from apps.companies.services import public_feed_urls
from apps.subscriptions.models import Subscription

from .forms import BillingInvoiceForm, BillingPaymentInvoiceForm, BillingPlanPriceForm, BillingProfileForm, SellerCreateForm, UserPlanUpdateForm, ProspectClientForm, ProspectActivityForm
from .forms import ProspectLinkClientForm


def create_manual_plan_order(user, currency):
    now = timezone.now()
    billing_profile = getattr(user, "billing_profile", None)
    customer_name = (
        getattr(billing_profile, "company_name", "")
        or user.company_name
        or user.get_full_name()
        or user.username
    )
    reference_token = uuid.uuid4().hex[:8].upper()
    safe_name = "-".join(customer_name.split())[:50]
    payment_reference = f"PRO-{reference_token}-{safe_name}"[:255]
    amount = settings.MANUAL_PRO_PRICE_PLN if currency == "pln" else settings.MANUAL_PRO_PRICE_EUR
    order = ManualPlanOrder.objects.create(
        user=user,
        amount=amount,
        currency=currency,
        payment_reference=payment_reference,
        payment_due_at=now + timedelta(days=14),
        access_until=now + timedelta(days=365),
    )
    activate_paid_plan(user, UserPlanTier.PRO)
    from apps.notifications.services import notify_manual_order_created

    notify_manual_order_created(order)
    return order


class LandingView(TemplateView):
    template_name = "landing.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        selected_currency = normalize_billing_currency(self.request.GET.get("currency"))
        context["selected_billing_currency"] = selected_currency
        context["billing_currencies"] = supported_billing_currencies()
        context["plus_price"] = plan_price_label(
            UserPlanTier.PLUS,
            settings.STRIPE_PLUS_PRICE_AMOUNT,
            selected_currency,
        )
        context["pro_price"] = plan_price_label(
            UserPlanTier.PRO,
            settings.STRIPE_PRO_PRICE_AMOUNT,
            selected_currency,
        )
        return context


class UserOrganizationQuerysetMixin(LoginRequiredMixin):
    def get_queryset(self):
        queryset = Organization.objects.select_related("owner", "subscription")
        if self.request.user.is_superuser:
            return queryset
        return queryset.filter(owner=self.request.user)

    def current_organization_count(self) -> int:
        return self.get_queryset().count()

    def current_organization_limit(self) -> int | None:
        if self.request.user.is_superuser:
            return None
        return self.request.user.organization_limit()

    def can_create_organization(self) -> bool:
        if self.request.user.is_superuser:
            return True
        return self.request.user.can_add_organization(self.current_organization_count())


class DashboardHomeView(UserOrganizationQuerysetMixin, TemplateView):
    template_name = "dashboard/home.html"

    def get_template_names(self):
        if self.request.user.is_superuser:
            return ["dashboard/home_admin.html"]
        if self.request.user.account_type == AccountType.STAFF:
            return ["dashboard/home_seller.html"]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        organizations = self.get_queryset()
        verified_organization_count = organizations.filter(
            verification_status=VerificationStatus.HUMAN_ADMIN_VERIFIED
        ).count()
        context["organizations"] = organizations
        context["organization_count"] = organizations.count()
        context["verified_organization_count"] = verified_organization_count
        context["pending_verification_count"] = organizations.count() - verified_organization_count
        context["organization_limit"] = self.current_organization_limit()
        context["can_create_organization"] = self.can_create_organization()
        return context


class OrganizationCreateView(UserOrganizationQuerysetMixin, CreateView):
    model = Organization
    form_class = OrganizationForm
    template_name = "dashboard/organization_form.html"
    success_url = reverse_lazy("dashboard:home")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not self.can_create_organization():
            if not request.user.has_selected_plan():
                if request.LANGUAGE_CODE == "pl":
                    messages.warning(request, "Najpierw wybierz plan, aby dodać stronę firmy.")
                else:
                    messages.warning(request, "Choose a plan first to add a company page.")
            else:
                if request.LANGUAGE_CODE == "pl":
                    messages.warning(request, "Osiągnięto limit stron dla Twojego planu.")
                else:
                    messages.warning(request, "Your plan page limit has been reached.")
            return redirect("dashboard:home")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["language_code"] = self.request.LANGUAGE_CODE
        from apps.subscriptions.models import PlanTier, PLAN_FEATURES
        tier_key = self.request.user.plan_tier or PlanTier.BASIC

        class SubscriptionHint:
            def feature_matrix(self):
                return PLAN_FEATURES.get(
                    tier_key,
                    PLAN_FEATURES[PlanTier.BASIC]
                )

        class OrganizationHint:
            def get_subscription(self):
                return SubscriptionHint()

        organization_hint = OrganizationHint()
        kwargs["organization"] = organization_hint
        return kwargs

    def form_valid(self, form):
        form.instance.owner = self.request.user
        messages.success(self.request, "Organization profile saved.")
        return super().form_valid(form)


class OrganizationUpdateView(UserOrganizationQuerysetMixin, UpdateView):
    model = Organization
    form_class = OrganizationForm
    template_name = "dashboard/organization_form.html"
    success_url = reverse_lazy("dashboard:home")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["language_code"] = self.request.LANGUAGE_CODE
        kwargs["organization"] = self.object
        return kwargs

    def form_valid(self, form):
        messages.success(self.request, "Organization profile updated.")
        return super().form_valid(form)


class OrganizationDeleteView(UserOrganizationQuerysetMixin, View):
    def post(self, request, pk, *args, **kwargs):
        organization = get_object_or_404(self.get_queryset(), pk=pk)
        organization_name = organization.name
        organization.delete()

        if request.LANGUAGE_CODE == "pl":
            messages.success(request, f"Usunięto stronę: {organization_name}.")
        else:
            messages.success(request, f"Deleted company page: {organization_name}.")

        return redirect("dashboard:home")

    def get(self, request, *args, **kwargs):
        return redirect("dashboard:home")


class PlanUpdateView(LoginRequiredMixin, FormView):
    form_class = UserPlanUpdateForm
    template_name = "dashboard/plan_form.html"
    success_url = reverse_lazy("dashboard:home")
    paid_tiers = {UserPlanTier.PLUS, UserPlanTier.PRO}

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_superuser:
            if request.LANGUAGE_CODE == "pl":
                messages.info(request, "Konto administratora nie korzysta z limitów planów.")
            else:
                messages.info(request, "Administrator account does not use plan limits.")
            return redirect("dashboard:home")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        if self.request.method == "GET":
            kwargs["initial"] = kwargs.get("initial", {})
            billing_profile = getattr(self.request.user, "billing_profile", None)
            profile_currency = billing_profile.billing_currency() if billing_profile else None
            kwargs["initial"]["billing_currency"] = normalize_billing_currency(self.request.GET.get("currency") or profile_currency)
            if self.request.user.manual_plan_orders.filter(
                status__in=[ManualPlanOrderStatus.AWAITING_PAYMENT, ManualPlanOrderStatus.PAID]
            ).exists():
                kwargs["initial"]["plan_tier"] = UserPlanUpdateForm.PRO_MANUAL
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["current_organization_count"] = self.request.user.organizations.count()
        context["current_organization_limit"] = self.request.user.organization_limit()
        billing_profile = getattr(self.request.user, "billing_profile", None)
        profile_currency = billing_profile.billing_currency() if billing_profile else None
        selected_currency = normalize_billing_currency(
            self.request.POST.get("billing_currency") if self.request.method == "POST" else self.request.GET.get("currency") or profile_currency
        )
        plus_price = get_active_plan_price(UserPlanTier.PLUS, selected_currency)
        pro_price = get_active_plan_price(UserPlanTier.PRO, selected_currency)
        context["selected_billing_currency"] = selected_currency
        context["billing_currencies"] = supported_billing_currencies()
        context["stripe_checkout_enabled"] = bool(settings.STRIPE_SECRET_KEY and (plus_price or pro_price))
        context["stripe_test_mode"] = settings.STRIPE_SECRET_KEY.startswith("sk_test_")
        context["plus_price_label"] = plan_price_label(
            UserPlanTier.PLUS,
            settings.STRIPE_PLUS_PRICE_AMOUNT,
            selected_currency,
        )
        context["pro_price_label"] = plan_price_label(
            UserPlanTier.PRO,
            settings.STRIPE_PRO_PRICE_AMOUNT,
            selected_currency,
        )
        context["plus_price_configured"] = bool(plus_price)
        context["pro_price_configured"] = bool(pro_price)
        context["billing_subscription"] = getattr(self.request.user, "billing_subscription", None)
        context["billing_profile"] = getattr(self.request.user, "billing_profile", None)
        manual_plan_order = self.request.user.manual_plan_orders.filter(
            status__in=[ManualPlanOrderStatus.AWAITING_PAYMENT, ManualPlanOrderStatus.PAID]
        ).first()
        context["manual_plan_order"] = manual_plan_order
        manual_currency = manual_plan_order.currency if manual_plan_order else selected_currency
        manual_amount = manual_plan_order.amount if manual_plan_order else (
            settings.MANUAL_PRO_PRICE_PLN if manual_currency == "pln" else settings.MANUAL_PRO_PRICE_EUR
        )
        context["manual_pro_price_label"] = format_amount(manual_amount, manual_currency)
        context["manual_payment_recipient"] = settings.MANUAL_PAYMENT_RECIPIENT
        context["manual_payment_bank"] = settings.MANUAL_PAYMENT_BANK
        context["manual_payment_iban"] = settings.MANUAL_PAYMENT_IBAN_PLN if manual_currency == "pln" else settings.MANUAL_PAYMENT_IBAN_EUR
        return context

    @staticmethod
    def _format_price_label(unit_amount: int, currency: str) -> str:
        return f"{unit_amount / 100:.2f} {currency.upper()}"

    def _build_checkout_urls(self) -> tuple[str, str]:
        success_url = (
            f"{settings.SITE_BASE_URL}"
            f"{reverse('dashboard:plan-checkout-success')}?session_id={{CHECKOUT_SESSION_ID}}"
        )
        cancel_url = f"{settings.SITE_BASE_URL}{reverse('dashboard:plan-checkout-cancel')}"
        return success_url, cancel_url

    def _create_checkout_session(self, selected_tier: str, plan_price: BillingPlanPrice):
        stripe.api_key = settings.STRIPE_SECRET_KEY
        success_url, cancel_url = self._build_checkout_urls()
        billing_profile = getattr(self.request.user, "billing_profile", None)
        billing_subscription = getattr(self.request.user, "billing_subscription", None)
        customer_id = getattr(billing_subscription, "stripe_customer_id", "") if billing_subscription else ""
        customer_kwargs = {}
        if customer_id:
            customer_kwargs["customer"] = customer_id
            customer_kwargs["customer_update"] = {"address": "auto", "name": "auto"}
        else:
            customer_kwargs["customer_email"] = getattr(billing_profile, "invoice_email", "") or self.request.user.email or None

        billing_metadata = {
            "billing_profile_id": str(billing_profile.pk),
            "billing_country": billing_profile.country.upper(),
            "billing_currency": plan_price.currency,
            "billing_customer_type": billing_profile.customer_type,
        }

        return stripe.checkout.Session.create(
            mode="subscription",
            payment_method_types=["card"],
            **customer_kwargs,
            billing_address_collection="required",
            tax_id_collection={"enabled": True},
            client_reference_id=str(self.request.user.pk),
            line_items=[
                {
                    "price": plan_price.stripe_price_id,
                    "quantity": 1,
                }
            ],
            success_url=success_url,
            cancel_url=cancel_url,
            subscription_data={
                "metadata": {
                    "user_id": str(self.request.user.pk),
                    "plan_tier": selected_tier,
                    "billing_plan_price_id": str(plan_price.pk),
                    **billing_metadata,
                }
            },
            metadata={
                "user_id": str(self.request.user.pk),
                "plan_tier": selected_tier,
                "billing_plan_price_id": str(plan_price.pk),
                **billing_metadata,
            },
        )

    def _create_plus_to_pro_upgrade_session(self, plan_price: BillingPlanPrice, billing_subscription: BillingSubscription):
        stripe.api_key = settings.STRIPE_SECRET_KEY
        success_url, cancel_url = self._build_checkout_urls()
        customer_kwargs = {}
        if billing_subscription.stripe_customer_id:
            customer_kwargs["customer"] = billing_subscription.stripe_customer_id
        else:
            customer_kwargs["customer_email"] = self.request.user.email or None

        return stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            **customer_kwargs,
            client_reference_id=str(self.request.user.pk),
            line_items=[
                {
                    "price_data": {
                        "currency": plan_price.currency,
                        "unit_amount": plan_price.amount,
                        "product_data": {
                            "name": "PRO yearly subscription plan change",
                        },
                    },
                    "quantity": 1,
                }
            ],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "user_id": str(self.request.user.pk),
                "plan_tier": UserPlanTier.PRO,
                "upgrade_type": "plus_to_pro",
                "billing_plan_price_id": str(plan_price.pk),
                "stripe_subscription_id": billing_subscription.stripe_subscription_id,
            },
        )

    def form_valid(self, form):
        selected_tier = form.cleaned_data["plan_tier"]
        selected_currency = form.cleaned_data["billing_currency"]
        user = self.request.user
        has_selected_plan = user.has_selected_plan()
        active_manual_order = user.manual_plan_orders.filter(
            status__in=[ManualPlanOrderStatus.AWAITING_PAYMENT, ManualPlanOrderStatus.PAID]
        ).first()

        if active_manual_order and selected_tier != UserPlanUpdateForm.PRO_MANUAL:
            messages.warning(self.request, "Plan Pro Manual jest aktywny. Inny plan będzie dostępny po jego wyłączeniu lub zakończeniu.")
            return redirect("dashboard:plan-update")

        if selected_tier == UserPlanUpdateForm.PRO_MANUAL:
            if active_manual_order:
                messages.info(self.request, "Masz już aktywny plan Pro Manual.")
                return redirect("dashboard:plan-update")
            if user.manual_plan_orders.filter(access_until__gt=timezone.now()).exists():
                messages.warning(self.request, "Nie można ponownie uruchomić Pro Manual przed końcem pierwotnego roku zamówienia.")
                return redirect("dashboard:plan-update")
            existing_subscription = getattr(user, "billing_subscription", None)
            if existing_subscription and existing_subscription.stripe_subscription_id and existing_subscription.status in {"active", "trialing", "past_due"}:
                messages.warning(self.request, "Nie można zamówić planu Pro Manual przy aktywnej subskrypcji Stripe.")
                return redirect("dashboard:billing-portal")
            billing_profile = getattr(user, "billing_profile", None)
            if not billing_profile or not billing_profile.is_complete():
                messages.warning(self.request, "Uzupełnij dane do faktury przed zamówieniem planu.")
                target = reverse("dashboard:manual-plan-confirm")
                return redirect(f"{reverse('dashboard:billing-profile')}?next={target}")
            return redirect("dashboard:manual-plan-confirm")

        if user.plan_tier == selected_tier and has_selected_plan:
            if self.request.LANGUAGE_CODE == "pl":
                messages.info(self.request, "Wybrany plan jest już aktywny.")
            else:
                messages.info(self.request, "This plan is already active.")
            return super().form_valid(form)

        if selected_tier == UserPlanTier.BASIC:
            billing_subscription = getattr(user, "billing_subscription", None)
            if (
                billing_subscription
                and billing_subscription.stripe_subscription_id
                and billing_subscription.status in {"active", "trialing", "past_due"}
            ):
                if self.request.LANGUAGE_CODE == "pl":
                    messages.info(self.request, "Zmiana na BASIC jest zablokowana w trakcie oplaconego okresu. Anuluj odnowienie i rozpocznij BASIC po zakonczeniu obecnego roku.")
                else:
                    messages.info(self.request, "Downgrading to BASIC is blocked during the paid period. Cancel renewal and start BASIC after the current year ends.")
                return redirect("dashboard:billing-portal")

            downgrade_to_basic(user)
            from apps.notifications.services import notify_plan_selected

            notify_plan_selected(user, UserPlanTier.BASIC)
            if self.request.LANGUAGE_CODE == "pl":
                messages.success(self.request, "Plan został zaktualizowany.")
            else:
                messages.success(self.request, "Plan updated successfully.")
            return super().form_valid(form)

        if selected_tier in self.paid_tiers:
            existing_subscription = getattr(user, "billing_subscription", None)
            if (
                existing_subscription
                and existing_subscription.stripe_subscription_id
                and existing_subscription.status in {"active", "trialing", "past_due"}
            ):
                if user.plan_tier == UserPlanTier.PRO and selected_tier == UserPlanTier.PLUS:
                    if self.request.LANGUAGE_CODE == "pl":
                        messages.info(self.request, "Zmiana z PRO na PLUS nie jest dostepna w trakcie oplaconego okresu. Anuluj odnowienie i rozpocznij PLUS po zakonczeniu obecnego roku.")
                    else:
                        messages.info(self.request, "Downgrading from PRO to PLUS is not available during a paid period. Cancel renewal and start PLUS after the current year ends.")
                    return redirect("dashboard:billing-portal")

                if user.plan_tier == UserPlanTier.PLUS and selected_tier == UserPlanTier.PRO:
                    billing_profile = getattr(user, "billing_profile", None)
                    selected_currency = (
                        existing_subscription.plan_price.currency
                        if existing_subscription.plan_price
                        else billing_profile.billing_currency() if billing_profile else form.cleaned_data["billing_currency"]
                    )
                    plan_price = get_active_plan_price(UserPlanTier.PRO, selected_currency)
                    if not plan_price:
                        messages.error(self.request, "No active PRO Stripe price is configured for your subscription currency.")
                        return redirect("dashboard:plan-update")
                    try:
                        checkout_session = self._create_plus_to_pro_upgrade_session(plan_price, existing_subscription)
                    except Exception:
                        messages.error(self.request, "Could not create Stripe upgrade payment session.")
                        return redirect("dashboard:plan-update")
                    checkout_url = getattr(checkout_session, "url", "")
                    if not checkout_url:
                        messages.error(self.request, "Stripe returned an invalid response.")
                        return redirect("dashboard:plan-update")
                    return redirect(checkout_url, permanent=False)

                if self.request.LANGUAGE_CODE == "pl":
                    messages.info(self.request, "Masz juz aktywna subskrypcje. Zmiany planu obsluzymy z poziomu zarzadzania subskrypcja.")
                else:
                    messages.info(self.request, "You already have an active subscription. Manage plan changes from the subscription page.")
                return redirect("dashboard:billing-portal")

            billing_profile = getattr(user, "billing_profile", None)
            if not billing_profile or not billing_profile.is_complete():
                if self.request.LANGUAGE_CODE == "pl":
                    messages.warning(self.request, "Uzupelnij dane do faktury przed platnoscia.")
                else:
                    messages.warning(self.request, "Complete billing details before payment.")
                return redirect("dashboard:billing-profile")

            if not settings.STRIPE_SECRET_KEY:
                if self.request.LANGUAGE_CODE == "pl":
                    messages.error(
                        self.request,
                        "Brak konfiguracji Stripe. Uzupełnij STRIPE_SECRET_KEY w zmiennych środowiskowych.",
                    )
                else:
                    messages.error(
                        self.request,
                        "Stripe is not configured. Set STRIPE_SECRET_KEY in environment variables.",
                )
                return redirect("dashboard:plan-update")

            selected_currency = billing_profile.billing_currency()
            plan_price = get_active_plan_price(selected_tier, selected_currency)
            if not plan_price:
                if self.request.LANGUAGE_CODE == "pl":
                    messages.error(self.request, "Brak aktywnej ceny Stripe dla wybranego planu.")
                else:
                    messages.error(self.request, "No active Stripe price is configured for the selected plan.")
                return redirect("dashboard:plan-update")

            try:
                checkout_session = self._create_checkout_session(selected_tier, plan_price)
            except Exception:
                if self.request.LANGUAGE_CODE == "pl":
                    messages.error(self.request, "Nie udało się utworzyć sesji płatności Stripe.")
                else:
                    messages.error(self.request, "Could not create Stripe checkout session.")
                return redirect("dashboard:plan-update")

            checkout_url = getattr(checkout_session, "url", "")
            if not checkout_url:
                if self.request.LANGUAGE_CODE == "pl":
                    messages.error(self.request, "Stripe zwrócił nieprawidłową odpowiedź.")
                else:
                    messages.error(self.request, "Stripe returned an invalid response.")
                return redirect("dashboard:plan-update")

            return redirect(checkout_url, permanent=False)

        if self.request.LANGUAGE_CODE == "pl":
            messages.error(self.request, "Nieobsługiwany plan.")
        else:
            messages.error(self.request, "Unsupported plan.")
        return redirect("dashboard:plan-update")


class PlanCheckoutSuccessView(LoginRequiredMixin, View):
    paid_tiers = {UserPlanTier.PLUS, UserPlanTier.PRO}

    def get(self, request, *args, **kwargs):
        checkout_session_id = request.GET.get("session_id")
        if not checkout_session_id:
            if request.LANGUAGE_CODE == "pl":
                messages.error(request, "Brakuje identyfikatora sesji Stripe.")
            else:
                messages.error(request, "Missing Stripe session id.")
            return redirect("dashboard:plan-update")

        if not settings.STRIPE_SECRET_KEY:
            if request.LANGUAGE_CODE == "pl":
                messages.error(request, "Stripe nie jest skonfigurowany.")
            else:
                messages.error(request, "Stripe is not configured.")
            return redirect("dashboard:plan-update")

        stripe.api_key = settings.STRIPE_SECRET_KEY

        try:
            checkout_session = stripe.checkout.Session.retrieve(checkout_session_id)
        except Exception:
            if request.LANGUAGE_CODE == "pl":
                messages.error(request, "Nie udało się zweryfikować płatności Stripe.")
            else:
                messages.error(request, "Could not verify Stripe payment.")
            return redirect("dashboard:plan-update")

        metadata = getattr(checkout_session, "metadata", {}) or {}
        session_user_id = metadata.get("user_id")
        selected_tier = metadata.get("plan_tier")
        upgrade_type = metadata.get("upgrade_type", "")
        payment_status = getattr(checkout_session, "payment_status", "")
        stripe_subscription_id = getattr(checkout_session, "subscription", "") or ""
        stripe_customer_id = getattr(checkout_session, "customer", "") or ""

        if session_user_id != str(request.user.pk):
            if request.LANGUAGE_CODE == "pl":
                messages.error(request, "Ta sesja płatności nie należy do Twojego konta.")
            else:
                messages.error(request, "This payment session does not belong to your account.")
            return redirect("dashboard:plan-update")

        if upgrade_type == "plus_to_pro":
            if selected_tier != UserPlanTier.PRO:
                messages.error(request, "Invalid upgrade returned from Stripe payment.")
                return redirect("dashboard:plan-update")
            if payment_status != "paid":
                messages.warning(request, "Upgrade payment has not been confirmed yet.")
                return redirect("dashboard:plan-update")

            billing_subscription = getattr(request.user, "billing_subscription", None)
            existing_subscription_id = metadata.get("stripe_subscription_id") or getattr(
                billing_subscription,
                "stripe_subscription_id",
                "",
            )
            plan_price = BillingPlanPrice.objects.filter(pk=metadata.get("billing_plan_price_id")).first()
            if not existing_subscription_id or not plan_price:
                messages.error(request, "Could not find the subscription or PRO price for this upgrade.")
                return redirect("dashboard:plan-update")

            try:
                stripe_subscription = stripe.Subscription.retrieve(existing_subscription_id)
                items = getattr(getattr(stripe_subscription, "items", None), "data", None)
                if items is None and isinstance(stripe_subscription, dict):
                    items = stripe_subscription.get("items", {}).get("data", [])
                first_item = items[0] if items else None
                item_id = getattr(first_item, "id", None) if first_item is not None else None
                if item_id is None and isinstance(first_item, dict):
                    item_id = first_item.get("id")
                if not item_id:
                    raise ValueError("Missing Stripe subscription item id")

                updated_subscription = stripe.Subscription.modify(
                    existing_subscription_id,
                    items=[{"id": item_id, "price": plan_price.stripe_price_id}],
                    billing_cycle_anchor="now",
                    proration_behavior="none",
                    cancel_at_period_end=False,
                    metadata={
                        "user_id": str(request.user.pk),
                        "plan_tier": UserPlanTier.PRO,
                        "billing_plan_price_id": str(plan_price.pk),
                    },
                )
                billing_subscription = sync_subscription_from_stripe(
                    updated_subscription,
                    fallback_user=request.user,
                    fallback_tier=UserPlanTier.PRO,
                )
            except Exception:
                if request.LANGUAGE_CODE == "pl":
                    messages.error(request, "Platnosc upgrade zostala przyjeta, ale nie udalo sie zaktualizowac subskrypcji Stripe. Skontaktuj sie z obsluga.")
                else:
                    messages.error(request, "Upgrade payment was accepted, but the Stripe subscription could not be updated. Please contact support.")
                return redirect("dashboard:billing-portal")

            if not billing_subscription:
                request.user.plan_tier = UserPlanTier.PRO
                request.user.plan_selected_at = timezone.now()
                request.user.paid_plan_started_at = timezone.now()
                request.user.save(update_fields=["plan_tier", "plan_selected_at", "paid_plan_started_at"])

            if request.LANGUAGE_CODE == "pl":
                messages.success(request, "Upgrade do PRO zakonczony. Nowy okres subskrypcji zaczyna sie od dzisiaj.")
            else:
                messages.success(request, "Upgrade to PRO completed. The new subscription period starts today.")
            return redirect("dashboard:home")

        if selected_tier not in self.paid_tiers:
            if request.LANGUAGE_CODE == "pl":
                messages.error(request, "Nieprawidłowy plan z płatności Stripe.")
            else:
                messages.error(request, "Invalid plan returned from Stripe payment.")
            return redirect("dashboard:plan-update")

        if payment_status != "paid" and not stripe_subscription_id:
            if request.LANGUAGE_CODE == "pl":
                messages.warning(request, "Płatność nie została jeszcze potwierdzona.")
            else:
                messages.warning(request, "Payment has not been confirmed yet.")
            return redirect("dashboard:plan-update")

        billing_subscription = None
        if stripe_subscription_id:
            try:
                stripe_subscription = stripe.Subscription.retrieve(stripe_subscription_id)
                billing_subscription = sync_subscription_from_stripe(
                    stripe_subscription,
                    fallback_user=request.user,
                    fallback_tier=selected_tier,
                )
                latest_invoice = (
                    stripe_subscription.get("latest_invoice")
                    if isinstance(stripe_subscription, dict)
                    else getattr(stripe_subscription, "latest_invoice", None)
                )
                latest_invoice_id = (
                    latest_invoice.get("id")
                    if isinstance(latest_invoice, dict)
                    else getattr(latest_invoice, "id", latest_invoice)
                )
                if latest_invoice_id:
                    try:
                        record_invoice_payment(stripe.Invoice.retrieve(latest_invoice_id))
                    except Exception:
                        # Webhook delivery remains the fallback when invoice retrieval is temporarily unavailable.
                        pass
            except Exception:
                billing_subscription = None

        if not billing_subscription and (request.user.plan_tier != selected_tier or request.user.plan_selected_at is None):
            plan_price = get_active_plan_price(selected_tier)
            BillingSubscription.objects.update_or_create(
                user=request.user,
                defaults={
                    "tier": selected_tier,
                    "plan_price": plan_price,
                    "stripe_customer_id": stripe_customer_id,
                    "stripe_subscription_id": stripe_subscription_id,
                    "stripe_price_id": plan_price.stripe_price_id if plan_price else "",
                    "status": "active",
                },
            )
            request.user.plan_tier = selected_tier
            if request.user.paid_plan_started_at is None:
                request.user.paid_plan_started_at = timezone.now()
            if request.user.plan_selected_at is None:
                request.user.plan_selected_at = timezone.now()
            request.user.save(update_fields=["plan_tier", "paid_plan_started_at", "plan_selected_at"])
            Subscription.objects.filter(organization__owner=request.user).update(tier=selected_tier)
            from apps.notifications.services import notify_plan_selected

            notify_plan_selected(request.user, selected_tier)

        if request.LANGUAGE_CODE == "pl":
            messages.success(request, "Płatność zakończona sukcesem. Plan został aktywowany.")
        else:
            messages.success(request, "Payment successful. Your plan is now active.")
        return redirect("dashboard:home")


class PlanCheckoutCancelView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        if request.LANGUAGE_CODE == "pl":
            messages.info(request, "Płatność została anulowana.")
        else:
            messages.info(request, "Payment was canceled.")
        return redirect("dashboard:plan-update")


class BillingPortalView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard/billing_subscription.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        billing_subscription = getattr(self.request.user, "billing_subscription", None)
        context["stripe_sync_error"] = False
        if (
            billing_subscription
            and billing_subscription.stripe_subscription_id
            and settings.STRIPE_SECRET_KEY
        ):
            stripe.api_key = settings.STRIPE_SECRET_KEY
            try:
                stripe_subscription = stripe.Subscription.retrieve(billing_subscription.stripe_subscription_id)
                billing_subscription = sync_subscription_from_stripe(
                    stripe_subscription,
                    fallback_user=self.request.user,
                    fallback_tier=billing_subscription.tier,
                ) or billing_subscription
            except Exception:
                context["stripe_sync_error"] = True
        context["billing_subscription"] = billing_subscription
        context["current_price"] = billing_subscription.plan_price if billing_subscription else None
        context["failed_payment"] = (
            BillingPayment.objects.filter(
                user=self.request.user,
                subscription=billing_subscription,
                status__in={"open", "failed"},
            )
            .order_by("-updated_at")
            .first()
            if billing_subscription and billing_subscription.status == "past_due"
            else None
        )
        return context


class StripeCustomerPortalView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        billing_subscription = getattr(request.user, "billing_subscription", None)
        customer_id = getattr(billing_subscription, "stripe_customer_id", "") if billing_subscription else ""
        if not settings.STRIPE_SECRET_KEY or not customer_id:
            messages.error(request, "Stripe payment management is not available for this account.")
            return redirect("dashboard:billing-portal")

        stripe.api_key = settings.STRIPE_SECRET_KEY
        try:
            session = stripe.billing_portal.Session.create(
                customer=customer_id,
                return_url=request.build_absolute_uri(reverse("dashboard:billing-portal")),
            )
        except Exception:
            messages.error(request, "Could not open Stripe payment management. Please try again.")
            return redirect("dashboard:billing-portal")

        return redirect(session.url)


class BillingSubscriptionCancelView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        billing_subscription = getattr(request.user, "billing_subscription", None)
        subscription_id = getattr(billing_subscription, "stripe_subscription_id", "") if billing_subscription else ""
        if not settings.STRIPE_SECRET_KEY or not subscription_id:
            if request.LANGUAGE_CODE == "pl":
                messages.error(request, "Nie znaleziono aktywnej subskrypcji Stripe dla tego konta.")
            else:
                messages.error(request, "No active Stripe subscription was found for this account.")
            return redirect("dashboard:plan-update")

        stripe.api_key = settings.STRIPE_SECRET_KEY
        try:
            stripe.Subscription.modify(subscription_id, cancel_at_period_end=True)
        except Exception:
            if request.LANGUAGE_CODE == "pl":
                messages.error(request, "Nie udalo sie anulowac odnowienia subskrypcji.")
            else:
                messages.error(request, "Could not cancel subscription renewal.")
            return redirect("dashboard:billing-portal")

        billing_subscription.cancel_at_period_end = True
        billing_subscription.save(update_fields=["cancel_at_period_end", "updated_at"])
        if request.LANGUAGE_CODE == "pl":
            messages.success(request, "Odnowienie subskrypcji zostalo anulowane. Dostep zostaje aktywny do konca oplaconego okresu.")
        else:
            messages.success(request, "Subscription renewal has been canceled. Access stays active until the end of the paid period.")
        return redirect("dashboard:billing-portal")


class BillingSubscriptionReactivateView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        billing_subscription = getattr(request.user, "billing_subscription", None)
        subscription_id = getattr(billing_subscription, "stripe_subscription_id", "") if billing_subscription else ""
        if not settings.STRIPE_SECRET_KEY or not subscription_id:
            if request.LANGUAGE_CODE == "pl":
                messages.error(request, "Nie znaleziono aktywnej subskrypcji Stripe dla tego konta.")
            else:
                messages.error(request, "No active Stripe subscription was found for this account.")
            return redirect("dashboard:plan-update")

        stripe.api_key = settings.STRIPE_SECRET_KEY
        try:
            stripe.Subscription.modify(subscription_id, cancel_at_period_end=False)
        except Exception:
            if request.LANGUAGE_CODE == "pl":
                messages.error(request, "Nie udalo sie wznowic odnowienia subskrypcji.")
            else:
                messages.error(request, "Could not reactivate subscription renewal.")
            return redirect("dashboard:billing-portal")

        billing_subscription.cancel_at_period_end = False
        billing_subscription.save(update_fields=["cancel_at_period_end", "updated_at"])
        if request.LANGUAGE_CODE == "pl":
            messages.success(request, "Odnowienie subskrypcji zostalo wlaczone ponownie.")
        else:
            messages.success(request, "Subscription renewal has been reactivated.")
        return redirect("dashboard:billing-portal")


class BillingProfileView(LoginRequiredMixin, UpdateView):
    model = BillingProfile
    form_class = BillingProfileForm
    template_name = "dashboard/billing_profile_form.html"
    success_url = reverse_lazy("dashboard:plan-update")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            expired_order = request.user.manual_plan_orders.filter(
                status__in=[ManualPlanOrderStatus.AWAITING_PAYMENT, ManualPlanOrderStatus.PAID],
                access_until__lte=timezone.now(),
            ).first()
            if expired_order:
                expired_order.status = ManualPlanOrderStatus.DISABLED
                expired_order.disabled_at = timezone.now()
                expired_order.save(update_fields=["status", "disabled_at", "updated_at"])
                downgrade_to_basic(request.user)
        if request.user.is_superuser:
            return redirect("dashboard:home")
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        profile, _ = BillingProfile.objects.get_or_create(
            user=self.request.user,
            defaults={
                "invoice_email": self.request.user.email,
                "street": "",
                "postal_code": "",
                "city": "",
                "country": "PL",
            },
        )
        return profile

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.instance.user = self.request.user
        if self.request.LANGUAGE_CODE == "pl":
            messages.success(self.request, "Dane do faktury zostaly zapisane.")
        else:
            messages.success(self.request, "Billing details have been saved.")
        return super().form_valid(form)

    def get_success_url(self):
        next_url = self.request.GET.get("next") or self.request.POST.get("next")
        if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={self.request.get_host()}):
            return next_url
        return reverse("dashboard:plan-update")


class ManualPlanConfirmView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard/manual_plan_confirm.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_superuser:
            return redirect("dashboard:home")
        billing_profile = getattr(request.user, "billing_profile", None)
        if not billing_profile or not billing_profile.is_complete():
            target = reverse("dashboard:manual-plan-confirm")
            return redirect(f"{reverse('dashboard:billing-profile')}?next={target}")
        if request.user.manual_plan_orders.filter(
            status__in=[ManualPlanOrderStatus.AWAITING_PAYMENT, ManualPlanOrderStatus.PAID]
        ).exists():
            messages.info(request, "Masz już aktywny plan Pro Manual.")
            return redirect("dashboard:plan-update")
        subscription = getattr(request.user, "billing_subscription", None)
        if subscription and subscription.stripe_subscription_id and subscription.status in {"active", "trialing", "past_due"}:
            messages.warning(request, "Nie można zamówić Pro Manual przy aktywnej subskrypcji Stripe.")
            return redirect("dashboard:billing-portal")
        if request.user.manual_plan_orders.filter(access_until__gt=timezone.now()).exists():
            messages.warning(request, "Nie można ponownie uruchomić Pro Manual przed końcem pierwotnego roku zamówienia.")
            return redirect("dashboard:plan-update")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = self.request.user.billing_profile
        currency = profile.billing_currency()
        amount = settings.MANUAL_PRO_PRICE_PLN if currency == "pln" else settings.MANUAL_PRO_PRICE_EUR
        context.update({
            "billing_profile": profile,
            "manual_pro_price_label": format_amount(amount, currency),
            "manual_payment_recipient": settings.MANUAL_PAYMENT_RECIPIENT,
            "manual_payment_bank": settings.MANUAL_PAYMENT_BANK,
            "manual_payment_iban": settings.MANUAL_PAYMENT_IBAN_PLN if currency == "pln" else settings.MANUAL_PAYMENT_IBAN_EUR,
        })
        return context

    def post(self, request, *args, **kwargs):
        profile = request.user.billing_profile
        create_manual_plan_order(request.user, profile.billing_currency())
        messages.success(request, "Plan Pro Manual został aktywowany. Wykonaj przelew w ciągu 14 dni.")
        return redirect("dashboard:plan-update")


@method_decorator(csrf_exempt, name="dispatch")
class StripeWebhookView(View):
    def post(self, request, *args, **kwargs):
        payload = request.body
        signature = request.META.get("HTTP_STRIPE_SIGNATURE", "")

        if settings.STRIPE_WEBHOOK_SECRET:
            try:
                event = stripe.Webhook.construct_event(payload, signature, settings.STRIPE_WEBHOOK_SECRET)
            except Exception:
                return HttpResponse(status=400)
        else:
            import json

            try:
                event = json.loads(payload.decode("utf-8"))
            except Exception:
                return HttpResponse(status=400)

        event_type = event.get("type") if isinstance(event, dict) else getattr(event, "type", "")
        data = event.get("data", {}) if isinstance(event, dict) else getattr(event, "data", {})
        stripe_object = data.get("object") if isinstance(data, dict) else getattr(data, "object", None)

        if event_type == "checkout.session.completed":
            metadata = stripe_object.get("metadata", {}) if isinstance(stripe_object, dict) else getattr(stripe_object, "metadata", {})
            subscription_id = stripe_object.get("subscription", "") if isinstance(stripe_object, dict) else getattr(stripe_object, "subscription", "")
            if subscription_id:
                try:
                    stripe.api_key = settings.STRIPE_SECRET_KEY
                    subscription = stripe.Subscription.retrieve(subscription_id)
                    sync_subscription_from_stripe(subscription)
                except Exception:
                    pass
            elif metadata:
                user = User.objects.filter(pk=metadata.get("user_id")).first()
                tier = metadata.get("plan_tier")
                if user and tier in {UserPlanTier.PLUS, UserPlanTier.PRO}:
                    sync_subscription_from_stripe(stripe_object, fallback_user=user, fallback_tier=tier)
        elif event_type in {
            "customer.subscription.created",
            "customer.subscription.updated",
            "customer.subscription.deleted",
        }:
            sync_subscription_from_stripe(stripe_object)
        elif event_type in {"invoice.paid", "invoice.payment_failed", "invoice.payment_action_required"}:
            record_invoice_payment(stripe_object)

        return HttpResponse(status=200)


class AdminRequiredMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not request.user.is_superuser:
            return redirect("dashboard:home")
        return super().dispatch(request, *args, **kwargs)


class BillingPriceManagementView(AdminRequiredMixin, FormView):
    form_class = BillingPlanPriceForm
    template_name = "dashboard/billing_price_management.html"
    success_url = reverse_lazy("dashboard:billing-price-management")

    def form_valid(self, form):
        price = form.save(commit=False)
        price.created_by = self.request.user
        price.save()
        if self.request.LANGUAGE_CODE == "pl":
            messages.success(self.request, "Cena planu zostala dodana.")
        else:
            messages.success(self.request, "Plan price has been added.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["prices"] = BillingPlanPrice.objects.select_related("created_by")
        return context


class BillingPriceArchiveView(AdminRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        price = get_object_or_404(BillingPlanPrice, pk=pk)
        price.active_for_new_customers = False
        price.save(update_fields=["active_for_new_customers", "updated_at"])
        if request.LANGUAGE_CODE == "pl":
            messages.success(request, "Cena zostala zarchiwizowana dla nowych klientow.")
        else:
            messages.success(request, "Price has been archived for new customers.")
        return redirect("dashboard:billing-price-management")


class BillingPriceActivateView(AdminRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        price = get_object_or_404(BillingPlanPrice, pk=pk)
        active_exists = BillingPlanPrice.objects.filter(
            tier=price.tier,
            currency=price.currency,
            active_for_new_customers=True,
        ).exclude(pk=price.pk).exists()
        if active_exists:
            if request.LANGUAGE_CODE == "pl":
                messages.error(request, "Ten plan ma juz aktywna cene dla tej waluty. Najpierw ja zarchiwizuj.")
            else:
                messages.error(request, "This plan already has an active price for this currency. Archive it first.")
            return redirect("dashboard:billing-price-management")

        price.active_for_new_customers = True
        price.save(update_fields=["active_for_new_customers", "updated_at"])
        if request.LANGUAGE_CODE == "pl":
            messages.success(request, "Cena jest aktywna dla nowych klientow.")
        else:
            messages.success(request, "Price is active for new customers.")
        return redirect("dashboard:billing-price-management")


class BillingPriceUpdateView(AdminRequiredMixin, UpdateView):
    model = BillingPlanPrice
    form_class = BillingPlanPriceForm
    template_name = "dashboard/billing_price_form.html"
    success_url = reverse_lazy("dashboard:billing-price-management")

    def form_valid(self, form):
        if self.request.LANGUAGE_CODE == "pl":
            messages.success(self.request, "Cena planu zostala zaktualizowana.")
        else:
            messages.success(self.request, "Plan price has been updated.")
        return super().form_valid(form)


class BillingOverviewView(AdminRequiredMixin, TemplateView):
    template_name = "dashboard/billing_overview.html"

    def get_context_data(self, **kwargs):
        from urllib.parse import urlencode

        context = super().get_context_data(**kwargs)
        manual_sort = self.request.GET.get("manual_sort", "-payment_due")
        subscription_sort = self.request.GET.get("subscription_sort", "customer")
        search_query = self.request.GET.get("q", "").strip()
        payments = BillingPayment.objects.select_related("user", "subscription")
        subscriptions = BillingSubscription.objects.select_related("user", "plan_price").prefetch_related("payments__invoices")
        manual_orders = list(ManualPlanOrder.objects.select_related("user", "disabled_by").prefetch_related("invoices"))
        now = timezone.now()

        subscription_rows = []
        for subscription in subscriptions:
            currency = subscription.plan_price.currency if subscription.plan_price else settings.STRIPE_CURRENCY
            current_price = get_active_plan_price(subscription.tier, currency)
            subscription.current_public_price_label = current_price.formatted_amount() if current_price else "-"
            paid_payments = [payment for payment in subscription.payments.all() if payment.status == "paid"]
            subscription.has_successful_payment = bool(paid_payments)
            subscription.latest_paid_payment = paid_payments[0] if paid_payments else None
            subscription.latest_manual_invoice = (
                next(iter(subscription.latest_paid_payment.invoices.all()), None)
                if subscription.latest_paid_payment
                else None
            )
            subscription_rows.append(subscription)

        for order in manual_orders:
            order.latest_manual_invoice = next(iter(order.invoices.all()), None)
        all_manual_orders = list(manual_orders)

        if search_query:
            needle = search_query.lower()

            def invoice_text(invoice):
                return invoice.invoice_number.lower() if invoice else ""

            manual_orders = [
                order for order in manual_orders
                if needle in " ".join([
                    order.user.email or "",
                    order.payment_reference or "",
                    order.status or "",
                    order.currency or "",
                    order.formatted_amount(),
                    invoice_text(order.latest_manual_invoice),
                    "Pro Manual",
                ]).lower()
            ]
            subscription_rows = [
                subscription for subscription in subscription_rows
                if needle in " ".join([
                    subscription.user.email or "",
                    subscription.get_tier_display(),
                    subscription.status or "",
                    subscription.current_public_price_label or "",
                    invoice_text(subscription.latest_manual_invoice),
                ]).lower()
            ]

        def none_safe(value):
            return value is None, value

        def invoice_sort_value(invoice):
            return (
                not bool(invoice),
                invoice.sent_at or invoice.issued_at if invoice else date.min,
                invoice.invoice_number if invoice else "",
            )

        manual_sort_map = {
            "customer": lambda order: (order.user.email or "").lower(),
            "-customer": lambda order: (order.user.email or "").lower(),
            "amount": lambda order: order.amount,
            "-amount": lambda order: order.amount,
            "status": lambda order: order.status,
            "-status": lambda order: order.status,
            "payment_due": lambda order: none_safe(order.payment_due_at),
            "-payment_due": lambda order: none_safe(order.payment_due_at),
            "access": lambda order: none_safe(order.access_until),
            "-access": lambda order: none_safe(order.access_until),
            "invoice": lambda order: invoice_sort_value(order.latest_manual_invoice),
            "-invoice": lambda order: invoice_sort_value(order.latest_manual_invoice),
        }
        subscription_sort_map = {
            "customer": lambda sub: (sub.user.email or "").lower(),
            "-customer": lambda sub: (sub.user.email or "").lower(),
            "plan": lambda sub: sub.tier,
            "-plan": lambda sub: sub.tier,
            "status": lambda sub: sub.status,
            "-status": lambda sub: sub.status,
            "price": lambda sub: sub.plan_price.amount if sub.plan_price else 0,
            "-price": lambda sub: sub.plan_price.amount if sub.plan_price else 0,
            "renewal": lambda sub: none_safe(sub.current_period_end),
            "-renewal": lambda sub: none_safe(sub.current_period_end),
            "payment": lambda sub: none_safe(sub.latest_payment_at),
            "-payment": lambda sub: none_safe(sub.latest_payment_at),
            "invoice": lambda sub: invoice_sort_value(sub.latest_manual_invoice),
            "-invoice": lambda sub: invoice_sort_value(sub.latest_manual_invoice),
        }
        if manual_sort not in manual_sort_map:
            manual_sort = "-payment_due"
        if subscription_sort not in subscription_sort_map:
            subscription_sort = "customer"
        manual_orders = sorted(manual_orders, key=manual_sort_map[manual_sort], reverse=manual_sort.startswith("-"))
        subscription_rows = sorted(subscription_rows, key=subscription_sort_map[subscription_sort], reverse=subscription_sort.startswith("-"))

        def billing_sort_url(param_name, current_sort, key):
            params = self.request.GET.copy()
            params[param_name] = f"-{key}" if current_sort == key else key
            return f"?{urlencode(params, doseq=True)}"

        context["subscriptions"] = subscription_rows
        context["payments"] = payments[:25]
        context["invoices"] = BillingInvoice.objects.select_related("user", "subscription")[:50]
        manual_paid = ManualPlanOrder.objects.filter(status=ManualPlanOrderStatus.PAID)
        total_turnover = (payments.filter(status="paid").aggregate(total=models.Sum("amount_paid"))["total"] or 0) + (manual_paid.aggregate(total=models.Sum("amount"))["total"] or 0)
        year_turnover = (payments.filter(status="paid", paid_at__year=now.year).aggregate(total=models.Sum("amount_paid"))["total"] or 0) + (manual_paid.filter(paid_at__year=now.year).aggregate(total=models.Sum("amount"))["total"] or 0)
        month_turnover = (payments.filter(status="paid", paid_at__year=now.year, paid_at__month=now.month).aggregate(total=models.Sum("amount_paid"))["total"] or 0) + (manual_paid.filter(paid_at__year=now.year, paid_at__month=now.month).aggregate(total=models.Sum("amount"))["total"] or 0)
        context["total_turnover_label"] = format_amount(total_turnover, settings.STRIPE_CURRENCY)
        context["year_turnover_label"] = format_amount(year_turnover, settings.STRIPE_CURRENCY)
        context["month_turnover_label"] = format_amount(month_turnover, settings.STRIPE_CURRENCY)
        context["active_subscription_count"] = subscriptions.filter(status__in=["active", "trialing"]).count() + sum(order.status in {ManualPlanOrderStatus.AWAITING_PAYMENT, ManualPlanOrderStatus.PAID} for order in all_manual_orders)
        context["canceling_subscription_count"] = subscriptions.filter(cancel_at_period_end=True).count()
        context["problem_subscription_count"] = subscriptions.filter(status__in=["past_due", "unpaid"]).count() + sum(order.is_overdue for order in all_manual_orders)
        context["manual_plan_orders"] = manual_orders
        context["billing_search_query"] = search_query
        context["manual_sort"] = manual_sort
        context["subscription_sort"] = subscription_sort
        context["manual_sort_urls"] = {
            "customer": billing_sort_url("manual_sort", manual_sort, "customer"),
            "amount": billing_sort_url("manual_sort", manual_sort, "amount"),
            "status": billing_sort_url("manual_sort", manual_sort, "status"),
            "payment_due": billing_sort_url("manual_sort", manual_sort, "payment_due"),
            "access": billing_sort_url("manual_sort", manual_sort, "access"),
            "invoice": billing_sort_url("manual_sort", manual_sort, "invoice"),
        }
        context["subscription_sort_urls"] = {
            "customer": billing_sort_url("subscription_sort", subscription_sort, "customer"),
            "plan": billing_sort_url("subscription_sort", subscription_sort, "plan"),
            "status": billing_sort_url("subscription_sort", subscription_sort, "status"),
            "price": billing_sort_url("subscription_sort", subscription_sort, "price"),
            "renewal": billing_sort_url("subscription_sort", subscription_sort, "renewal"),
            "payment": billing_sort_url("subscription_sort", subscription_sort, "payment"),
            "invoice": billing_sort_url("subscription_sort", subscription_sort, "invoice"),
        }
        context["manual_payment_recipient"] = settings.MANUAL_PAYMENT_RECIPIENT
        return context


class BillingInvoicesAdminView(AdminRequiredMixin, TemplateView):
    template_name = "dashboard/billing_invoices_admin.html"

    def get_context_data(self, **kwargs):
        from urllib.parse import urlencode

        context = super().get_context_data(**kwargs)
        sort = self.request.GET.get("sort", "-paid")
        search_query = self.request.GET.get("q", "").strip()
        rows = []
        for payment in BillingPayment.objects.filter(status="paid").select_related("user", "subscription").prefetch_related("invoices"):
            invoice = next(iter(payment.invoices.all()), None)
            rows.append({
                "kind": "stripe",
                "object": payment,
                "user": payment.user,
                "plan": payment.subscription.get_tier_display() if payment.subscription else "Stripe",
                "amount": payment.formatted_amount(),
                "amount_value": payment.amount_paid,
                "paid_at": payment.paid_at,
                "invoice": invoice,
            })
        for order in ManualPlanOrder.objects.filter(status=ManualPlanOrderStatus.PAID).select_related("user").prefetch_related("invoices"):
            invoice = next(iter(order.invoices.all()), None)
            rows.append({
                "kind": "manual",
                "object": order,
                "user": order.user,
                "plan": "Pro Manual",
                "amount": order.formatted_amount(),
                "amount_value": order.amount,
                "paid_at": order.paid_at,
                "invoice": invoice,
            })
        sort_map = {
            "customer": lambda row: (row["user"].email or "").lower(),
            "-customer": lambda row: (row["user"].email or "").lower(),
            "plan": lambda row: row["plan"],
            "-plan": lambda row: row["plan"],
            "amount": lambda row: row["amount_value"],
            "-amount": lambda row: row["amount_value"],
            "paid": lambda row: row["paid_at"] or timezone.now(),
            "-paid": lambda row: row["paid_at"] or timezone.now(),
            "invoice": lambda row: (
                not bool(row["invoice"]),
                row["invoice"].sent_at or row["invoice"].issued_at if row["invoice"] else date.min,
                row["invoice"].invoice_number if row["invoice"] else "",
            ),
            "-invoice": lambda row: (
                not bool(row["invoice"]),
                row["invoice"].sent_at or row["invoice"].issued_at if row["invoice"] else date.min,
                row["invoice"].invoice_number if row["invoice"] else "",
            ),
        }
        if sort not in sort_map:
            sort = "-paid"

        if search_query:
            needle = search_query.lower()
            rows = [
                row for row in rows
                if needle in " ".join([
                    row["user"].email or "",
                    row["plan"] or "",
                    row["amount"] or "",
                    row["kind"] or "",
                    row["invoice"].invoice_number if row["invoice"] else "",
                    "paid",
                ]).lower()
            ]

        def sort_url(key):
            params = self.request.GET.copy()
            params["sort"] = f"-{key}" if sort == key else key
            return f"?{urlencode(params, doseq=True)}"

        context["invoice_rows"] = sorted(rows, key=sort_map[sort], reverse=sort.startswith("-"))
        context["current_sort"] = sort
        context["search_query"] = search_query
        context["sort_urls"] = {
            "customer": sort_url("customer"),
            "plan": sort_url("plan"),
            "amount": sort_url("amount"),
            "paid": sort_url("paid"),
            "invoice": sort_url("invoice"),
        }
        context["missing_invoice_count"] = sum(not row["invoice"] for row in rows)
        return context


class BillingCustomerInvoicesDetailView(AdminRequiredMixin, TemplateView):
    template_name = "dashboard/billing_customer_invoices_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        customer = get_object_or_404(User, pk=self.kwargs["pk"])
        context["customer"] = customer
        context["billing_profile"] = getattr(customer, "billing_profile", None)
        context["invoices"] = BillingInvoice.objects.filter(user=customer).select_related(
            "payment", "manual_order", "subscription"
        )
        return context


class ManualPlanMarkPaidView(AdminRequiredMixin, View):
    def post(self, request, pk):
        order = get_object_or_404(ManualPlanOrder, pk=pk, status=ManualPlanOrderStatus.AWAITING_PAYMENT)
        order.status = ManualPlanOrderStatus.PAID
        order.paid_at = timezone.now()
        order.save(update_fields=["status", "paid_at", "updated_at"])
        from apps.notifications.services import close_manual_order_overdue, notify_invoice_needed_for_manual_order

        close_manual_order_overdue(order, closed_by=request.user)
        notify_invoice_needed_for_manual_order(order)
        messages.success(request, f"Potwierdzono płatność {order.payment_reference}.")
        return redirect("dashboard:billing-overview")


class ManualPlanDisableView(AdminRequiredMixin, View):
    def post(self, request, pk):
        order = get_object_or_404(
            ManualPlanOrder,
            pk=pk,
            status__in=[ManualPlanOrderStatus.AWAITING_PAYMENT, ManualPlanOrderStatus.PAID],
        )
        order.status = ManualPlanOrderStatus.DISABLED
        order.disabled_at = timezone.now()
        order.disabled_by = request.user
        order.save(update_fields=["status", "disabled_at", "disabled_by", "updated_at"])
        downgrade_to_basic(order.user)
        from apps.notifications.services import close_manual_order_overdue

        close_manual_order_overdue(order, closed_by=request.user)
        messages.success(request, f"Wyłączono plan {order.payment_reference}.")
        return redirect("dashboard:billing-overview")


class ManualPlanInvoiceCreateView(AdminRequiredMixin, View):
    def post(self, request, pk):
        order = get_object_or_404(ManualPlanOrder, pk=pk, status=ManualPlanOrderStatus.PAID)
        form = BillingInvoiceForm(request.POST, request.FILES)
        if form.is_valid():
            invoice = form.save(commit=False)
            invoice.user = order.user
            invoice.manual_order = order
            invoice.sent = True
            invoice.save()
            from apps.notifications.services import close_invoice_needed_for_manual_order

            close_invoice_needed_for_manual_order(order, closed_by=request.user)
            messages.success(request, "Faktura dla Pro Manual została zapisana.")
        else:
            messages.error(request, "Nie udało się zapisać faktury: " + " ".join(form.errors.as_text().splitlines()))
        return redirect("dashboard:billing-invoices-admin")


class StripePaymentInvoiceCreateView(AdminRequiredMixin, View):
    def post(self, request, pk):
        payment = get_object_or_404(BillingPayment, pk=pk, status="paid")
        form = BillingInvoiceForm(request.POST, request.FILES)
        if form.is_valid():
            invoice = form.save(commit=False)
            invoice.user = payment.user
            invoice.subscription = payment.subscription
            invoice.payment = payment
            invoice.sent = True
            invoice.save()
            from apps.notifications.services import close_invoice_needed_for_payment

            close_invoice_needed_for_payment(payment, closed_by=request.user)
            messages.success(request, "Faktura dla płatności Stripe została zapisana.")
        else:
            messages.error(request, "Nie udało się zapisać faktury: " + " ".join(form.errors.as_text().splitlines()))
        return redirect("dashboard:billing-invoices-admin")


class BillingInvoiceCreateView(AdminRequiredMixin, View):
    def post(self, request, subscription_pk):
        subscription = get_object_or_404(BillingSubscription, pk=subscription_pk)
        if subscription.tier == UserPlanTier.BASIC or not subscription.payments.filter(status="paid").exists():
            messages.error(request, "An invoice can only be added after Stripe records a successful paid-plan payment.")
            return redirect("dashboard:billing-overview")
        form = BillingInvoiceForm(request.POST, request.FILES)
        if form.is_valid():
            invoice = form.save(commit=False)
            invoice.user = subscription.user
            invoice.subscription = subscription
            invoice.sent = True
            invoice.save()
            latest_payment = subscription.payments.filter(status="paid").first()
            if latest_payment:
                from apps.notifications.services import close_invoice_needed_for_payment

                close_invoice_needed_for_payment(latest_payment, closed_by=request.user)
            messages.success(request, "Invoice has been added.")
        else:
            messages.error(request, "Invoice could not be added: " + " ".join(form.errors.as_text().splitlines()))
        return redirect("dashboard:billing-overview")


class BillingPaymentInvoiceUpdateView(AdminRequiredMixin, UpdateView):
    model = BillingPayment
    form_class = BillingPaymentInvoiceForm
    http_method_names = ["post"]

    def form_valid(self, form):
        if form.instance.invoice_issued or form.instance.invoice_document:
            from apps.notifications.services import close_invoice_needed_for_payment

            close_invoice_needed_for_payment(form.instance, closed_by=self.request.user)
        messages.success(self.request, "Invoice information has been updated.")
        return super().form_valid(form)

    def form_invalid(self, form):
        messages.error(self.request, "Invoice information could not be updated: " + " ".join(form.errors.as_text().splitlines()))
        return redirect("dashboard:billing-overview")

    def get_success_url(self):
        return reverse("dashboard:billing-overview")


class BillingInvoiceAdminDownloadView(AdminRequiredMixin, View):
    def get(self, request, pk):
        invoice = get_object_or_404(BillingInvoice, pk=pk)
        return FileResponse(
            invoice.document.open("rb"),
            as_attachment=True,
            filename=invoice.document.name.rsplit("/", 1)[-1],
            content_type="application/pdf",
        )


class CustomerInvoiceListView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard/customer_invoices.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["invoices"] = BillingInvoice.objects.filter(user=self.request.user)
        context["billing_profile"] = getattr(self.request.user, "billing_profile", None)
        return context


class CustomerInvoiceDownloadView(LoginRequiredMixin, View):
    def get(self, request, pk):
        invoice = get_object_or_404(BillingInvoice, pk=pk, user=request.user)
        return FileResponse(
            invoice.document.open("rb"),
            as_attachment=True,
            filename=invoice.document.name.rsplit("/", 1)[-1],
            content_type="application/pdf",
        )


class ClientListView(AdminRequiredMixin, TemplateView):
    template_name = "dashboard/client_list.html"

    def get_context_data(self, **kwargs):
        from apps.sales.models import ProspectClient
        from urllib.parse import urlencode

        context = super().get_context_data(**kwargs)
        q = self.request.GET.get("q", "").strip()
        sort = self.request.GET.get("sort", "email")
        linked_prospect_subquery = ProspectClient.objects.filter(
            registered_client=models.OuterRef("pk")
        ).values("pk")[:1]
        linked_seller_subquery = ProspectClient.objects.filter(
            registered_client=models.OuterRef("pk")
        ).values("seller__username")[:1]
        first_organization_name_subquery = Organization.objects.filter(
            owner=models.OuterRef("pk")
        ).order_by("name").values("name")[:1]
        first_organization_website_subquery = Organization.objects.filter(
            owner=models.OuterRef("pk")
        ).order_by("name").values("website_url")[:1]
        verified_organization_subquery = Organization.objects.filter(
            owner=models.OuterRef("pk"),
            verification_status=VerificationStatus.HUMAN_ADMIN_VERIFIED,
        )
        qs = (
            User.objects.filter(is_superuser=False, account_type=AccountType.CLIENT)
            .annotate(linked_prospect_id=models.Subquery(linked_prospect_subquery))
            .annotate(linked_seller_username=models.Subquery(linked_seller_subquery))
            .annotate(primary_organization_name=models.Subquery(first_organization_name_subquery))
            .annotate(primary_organization_website=models.Subquery(first_organization_website_subquery))
            .annotate(is_verified=models.Exists(verified_organization_subquery))
            .annotate(organization_count=models.Count("organizations", distinct=True))
            .annotate(verified_organization_count=models.Count(
                "organizations",
                filter=models.Q(organizations__verification_status=VerificationStatus.HUMAN_ADMIN_VERIFIED),
                distinct=True,
            ))
            .annotate(last_reviewed_at=models.Max("organizations__last_reviewed_at"))
            .annotate(last_invoice_sent_at=models.Max("billing_invoices__sent_at"))
            .annotate(last_invoice_issued_at=models.Max("billing_invoices__issued_at"))
            .prefetch_related("organizations")
        )
        if q:
            qs = qs.filter(
                models.Q(company_name__icontains=q)
                | models.Q(email__icontains=q)
                | models.Q(username__icontains=q)
            )
        sort_map = {
            "company": ("company_name", "email"),
            "-company": ("-company_name", "email"),
            "email": ("email",),
            "-email": ("-email",),
            "plan": ("plan_tier", "email"),
            "-plan": ("-plan_tier", "email"),
            "verified": ("is_verified", "email"),
            "-verified": ("-is_verified", "email"),
            "seller": ("linked_seller_username", "email"),
            "-seller": ("-linked_seller_username", "email"),
            "country": ("country", "email"),
            "-country": ("-country", "email"),
            "pages": ("organization_count", "email"),
            "-pages": ("-organization_count", "email"),
            "joined": ("date_joined", "email"),
            "-joined": ("-date_joined", "email"),
            "login": ("last_login", "email"),
            "-login": ("-last_login", "email"),
            "reviewed": ("last_reviewed_at", "email"),
            "-reviewed": ("-last_reviewed_at", "email"),
            "invoice": ("last_invoice_sent_at", "last_invoice_issued_at", "email"),
            "-invoice": ("-last_invoice_sent_at", "-last_invoice_issued_at", "email"),
        }
        if sort not in sort_map:
            sort = "email"
        qs = qs.order_by(*sort_map[sort])

        def sort_url(key):
            next_sort = key if sort != key else f"-{key}"
            params = {"sort": next_sort}
            if q:
                params["q"] = q
            return f"?{urlencode(params)}"

        context["clients"] = qs
        context["search_query"] = q
        context["current_sort"] = sort
        context["sort_urls"] = {
            "company": sort_url("company"),
            "email": sort_url("email"),
            "plan": sort_url("plan"),
            "verified": sort_url("verified"),
            "seller": sort_url("seller"),
            "country": sort_url("country"),
            "pages": sort_url("pages"),
            "joined": sort_url("joined"),
            "login": sort_url("login"),
            "reviewed": sort_url("reviewed"),
            "invoice": sort_url("invoice"),
        }
        return context


class ClientVerifyView(AdminRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        client = get_object_or_404(
            User,
            pk=pk,
            is_superuser=False,
            account_type=AccountType.CLIENT,
        )
        organizations = list(client.organizations.all().order_by("name"))
        if not organizations:
            if request.LANGUAGE_CODE == "pl":
                messages.error(request, "Ten klient nie ma jeszcze zadnej strony do weryfikacji.")
            else:
                messages.error(request, "This client does not have any company page to verify yet.")
            return redirect(self._next_url(request))

        for organization in organizations:
            organization.verification_status = VerificationStatus.HUMAN_ADMIN_VERIFIED
            if organization.verified_at is None:
                organization.verified_at = timezone.now()
            if organization.verified_by_id is None:
                organization.verified_by = request.user
            organization.save(update_fields=["verification_status", "verified_at", "verified_by", "updated_at"])

        if request.LANGUAGE_CODE == "pl":
            messages.success(request, "Klient zostal oznaczony jako verified.")
        else:
            messages.success(request, "Client has been marked as verified.")
        return redirect(self._next_url(request))

    def _next_url(self, request):
        next_url = request.POST.get("next") or reverse("dashboard:client-list")
        if url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
            return next_url
        return reverse("dashboard:client-list")


class OrganizationReviewView(AdminRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        organization = get_object_or_404(
            Organization.objects.select_related("owner"),
            pk=pk,
            verification_status=VerificationStatus.HUMAN_ADMIN_VERIFIED,
        )
        organization.last_reviewed_at = timezone.now()
        organization.last_reviewed_by = request.user
        organization.save(update_fields=["last_reviewed_at", "last_reviewed_by", "updated_at"])

        if request.LANGUAGE_CODE == "pl":
            messages.success(request, f"Oznaczono jako sprawdzone: {organization.name}.")
        else:
            messages.success(request, f"Marked as reviewed: {organization.name}.")

        return redirect(self._next_url(request, organization.owner_id))

    def _next_url(self, request, owner_id):
        next_url = request.POST.get("next") or reverse("dashboard:client-detail", kwargs={"pk": owner_id})
        if url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
            return next_url
        return reverse("dashboard:client-detail", kwargs={"pk": owner_id})


class ClientChangeSellerView(AdminRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        from apps.sales.models import ProspectClient

        client = get_object_or_404(
            User,
            pk=pk,
            is_superuser=False,
            account_type=AccountType.CLIENT,
        )
        seller_id = request.POST.get("seller_id", "").strip()
        if not seller_id:
            if request.LANGUAGE_CODE == "pl":
                messages.error(request, "Nie wybrano opiekuna.")
            else:
                messages.error(request, "No seller selected.")
            return redirect(reverse("dashboard:client-detail", kwargs={"pk": pk}))

        new_seller = get_object_or_404(
            User.objects.filter(
                models.Q(account_type=AccountType.STAFF) | models.Q(is_superuser=True)
            ),
            pk=seller_id,
        )

        attributed_prospect = getattr(client, "attributed_prospect", None)
        settlement = getattr(client, "seller_settlement", None)

        if attributed_prospect is not None:
            attributed_prospect.seller = new_seller
            attributed_prospect.save(update_fields=["seller", "updated_at"])
        elif settlement is not None:
            settlement.seller = new_seller
            settlement.save(update_fields=["seller"])
        else:
            ProspectClient.objects.create(
                seller=new_seller,
                registered_client=client,
                company_name=client.company_name or client.email,
                contact_person=client.email,
                email=client.email,
                phone="",
            )

        if request.LANGUAGE_CODE == "pl":
            messages.success(request, f"Opiekun klienta zmieniony na: {new_seller.username}.")
        else:
            messages.success(request, f"Account owner changed to: {new_seller.username}.")
        return redirect(reverse("dashboard:client-detail", kwargs={"pk": pk}))


class OrganizationVerifyView(AdminRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        organization = get_object_or_404(
            Organization.objects.select_related("owner"),
            pk=pk,
        )
        action = request.POST.get("action", "verify")
        if action == "unverify":
            organization.verification_status = VerificationStatus.UNVERIFIED
            organization.save(update_fields=["verification_status", "updated_at"])
            if request.LANGUAGE_CODE == "pl":
                messages.success(request, f"Cofnięto weryfikację: {organization.name}.")
            else:
                messages.success(request, f"Verification removed: {organization.name}.")
        else:
            organization.verification_status = VerificationStatus.HUMAN_ADMIN_VERIFIED
            if organization.verified_at is None:
                organization.verified_at = timezone.now()
            if organization.verified_by_id is None:
                organization.verified_by = request.user
            organization.save(update_fields=["verification_status", "verified_at", "verified_by", "updated_at"])
            if request.LANGUAGE_CODE == "pl":
                messages.success(request, f"Strona zweryfikowana: {organization.name}.")
            else:
                messages.success(request, f"Page verified: {organization.name}.")

        return redirect(self._next_url(request, organization.owner_id))

    def _next_url(self, request, owner_id):
        next_url = request.POST.get("next") or reverse("dashboard:client-detail", kwargs={"pk": owner_id})
        if url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
            return next_url
        return reverse("dashboard:client-detail", kwargs={"pk": owner_id})


class ClientDetailView(AdminRequiredMixin, TemplateView):
    template_name = "dashboard/client_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        client = get_object_or_404(
            User.objects.select_related("seller_settlement__seller", "attributed_prospect__seller"),
            pk=self.kwargs["pk"],
            is_superuser=False,
            account_type=AccountType.CLIENT,
        )
        organizations = list(client.organizations.all().order_by("name"))
        settlement = getattr(client, "seller_settlement", None)
        attributed_prospect = getattr(client, "attributed_prospect", None)
        seller = None
        if settlement is not None:
            seller = settlement.seller
        elif attributed_prospect is not None:
            seller = attributed_prospect.seller

        primary_organization = organizations[0] if organizations else None
        phone_number = ""
        address_parts = []
        if attributed_prospect is not None and attributed_prospect.phone:
            phone_number = attributed_prospect.phone
        elif primary_organization is not None:
            phone_number = primary_organization.phone_number

        if primary_organization is not None:
            address_parts = [
                primary_organization.address_line,
                primary_organization.postal_code,
                primary_organization.city,
                primary_organization.country,
            ]

        available_sellers = User.objects.filter(
            models.Q(account_type=AccountType.STAFF) | models.Q(is_superuser=True)
        ).order_by("username")

        context["client"] = client
        context["client_seller"] = seller
        context["client_prospect"] = attributed_prospect
        context["client_phone_number"] = phone_number
        context["client_address"] = ", ".join(part for part in address_parts if part)
        context["available_sellers"] = available_sellers
        context["organizations"] = [
            {
                "organization": organization,
                "public_urls": public_feed_urls(organization, self.request),
                "supports_jsonld": organization.supports_advanced_formats,
                "supports_company_md": organization.supports_company_md,
                "supports_llms_txt": organization.supports_llms_txt,
            }
            for organization in organizations
        ]
        return context


class SellerListView(AdminRequiredMixin, FormView):
    template_name = "dashboard/seller_list.html"
    form_class = SellerCreateForm
    success_url = reverse_lazy("dashboard:seller-list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["language_code"] = self.request.LANGUAGE_CODE
        return kwargs

    def _seller_queryset(self):
        q = self.request.GET.get("q", "").strip()
        qs = (
            User.objects.filter(account_type=AccountType.STAFF, is_superuser=False)
            .order_by("username")
        )
        if q:
            qs = qs.filter(
                models.Q(username__icontains=q)
                | models.Q(email__icontains=q)
            )
        return qs, q

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        sellers, search_query = self._seller_queryset()
        context["sellers"] = sellers
        context["search_query"] = search_query
        return context

    def form_valid(self, form):
        seller = form.save()
        if self.request.LANGUAGE_CODE == "pl":
            messages.success(self.request, f"Dodano sprzedawcę: {seller.username}.")
        else:
            messages.success(self.request, f"Seller added: {seller.username}.")
        return super().form_valid(form)


class SellerDetailView(AdminRequiredMixin, TemplateView):
    template_name = "dashboard/seller_detail.html"

    def get_context_data(self, **kwargs):
        from apps.sales.models import ProspectActivity

        context = super().get_context_data(**kwargs)
        seller = get_object_or_404(
            User,
            pk=self.kwargs["pk"],
            account_type=AccountType.STAFF,
            is_superuser=False,
        )
        context["seller"] = seller
        context["activities"] = ProspectActivity.objects.select_related(
            "prospect",
            "prospect__registered_client",
        ).filter(
            seller=seller,
        ).order_by(
            "-activity_date",
            "-created_at",
        )
        return context


class SellerAccessToggleView(AdminRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        seller = get_object_or_404(
            User,
            pk=pk,
            account_type=AccountType.STAFF,
            is_superuser=False,
        )
        seller.is_active = not seller.is_active
        seller.save(update_fields=["is_active"])

        if request.LANGUAGE_CODE == "pl":
            if seller.is_active:
                messages.success(request, f"Odblokowano dostęp dla: {seller.username}.")
            else:
                messages.success(request, f"Zablokowano dostęp dla: {seller.username}.")
        else:
            if seller.is_active:
                messages.success(request, f"Access enabled for: {seller.username}.")
            else:
                messages.success(request, f"Access blocked for: {seller.username}.")

        return redirect("dashboard:seller-detail", pk=seller.pk)


class SellerDeleteView(AdminRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        seller = get_object_or_404(
            User,
            pk=pk,
            account_type=AccountType.STAFF,
            is_superuser=False,
        )
        username = seller.username
        seller.delete()

        if request.LANGUAGE_CODE == "pl":
            messages.success(request, f"Usunięto sprzedawcę: {username}.")
        else:
            messages.success(request, f"Seller deleted: {username}.")

        return redirect("dashboard:seller-list")


class SellerSettlementListView(AdminRequiredMixin, TemplateView):
    template_name = "dashboard/seller_settlements.html"

    def get_context_data(self, **kwargs):
        from apps.sales.models import ProspectClient, SellerSettlement

        context = super().get_context_data(**kwargs)
        seller_id = self.request.GET.get("seller")

        sellers = User.objects.filter(
            account_type=AccountType.STAFF,
            is_superuser=False,
        ).order_by("username")

        selected_seller = None
        if seller_id:
            selected_seller = sellers.filter(pk=seller_id).first()

        payable_qs = ProspectClient.objects.select_related("seller", "registered_client").filter(
            registered_client__isnull=False,
            registered_client__account_type=AccountType.CLIENT,
            registered_client__plan_tier__in=[UserPlanTier.PLUS, UserPlanTier.PRO],
            registered_client__seller_settlement__isnull=True,
        )

        if selected_seller:
            payable_qs = payable_qs.filter(seller=selected_seller)

        settled_qs = SellerSettlement.objects.select_related("seller", "client", "prospect").all()
        if selected_seller:
            settled_qs = settled_qs.filter(seller=selected_seller)

        context["sellers"] = sellers
        context["selected_seller"] = selected_seller
        context["payable_prospects"] = payable_qs.order_by("registered_client__date_joined")
        context["settled_items"] = settled_qs
        return context


class SellerSettlementCreateView(AdminRequiredMixin, View):
    def post(self, request, prospect_pk, *args, **kwargs):
        from apps.sales.models import ProspectClient, SellerSettlement

        prospect = get_object_or_404(
            ProspectClient.objects.select_related("seller", "registered_client"),
            pk=prospect_pk,
            registered_client__isnull=False,
        )
        client = prospect.registered_client

        if client.plan_tier not in [UserPlanTier.PLUS, UserPlanTier.PRO]:
            if request.LANGUAGE_CODE == "pl":
                messages.error(request, "Klient nie ma planu płatnego (PLUS/PRO).")
            else:
                messages.error(request, "Client is not on a paid plan (PLUS/PRO).")
            return redirect("dashboard:seller-settlements")

        settlement, created = SellerSettlement.objects.get_or_create(
            client=client,
            defaults={
                "seller": prospect.seller,
                "prospect": prospect,
                "client_registered_at": client.date_joined,
                "client_paid_plan_started_at": client.paid_plan_started_at,
                "client_plan_tier": client.plan_tier,
                "settled_by": request.user,
            },
        )

        if created:
            if request.LANGUAGE_CODE == "pl":
                messages.success(request, "Klient został rozliczony.")
            else:
                messages.success(request, "Client has been settled.")
        else:
            if request.LANGUAGE_CODE == "pl":
                messages.info(request, "Ten klient został już wcześniej rozliczony.")
            else:
                messages.info(request, "This client has already been settled.")

        return redirect("dashboard:seller-settlements")


class SellerActivityReportView(AdminRequiredMixin, TemplateView):
    template_name = "dashboard/report_seller_activities.html"

    def _show_all_history(self) -> bool:
        return self.request.GET.get("scope") == "all"

    def _selected_month(self) -> str:
        if self._show_all_history():
            return ""

        selected_month = self.request.GET.get("month", "").strip()
        if selected_month:
            return selected_month
        return timezone.localdate().strftime("%Y-%m")

    def _month_range(self, month_value: str) -> tuple[date, date] | None:
        if not month_value:
            return None
        try:
            year_str, month_str = month_value.split("-", 1)
            year = int(year_str)
            month = int(month_str)
            month_start = date(year, month, 1)
        except (TypeError, ValueError):
            return None

        if month == 12:
            next_month = date(year + 1, 1, 1)
        else:
            next_month = date(year, month + 1, 1)
        return month_start, next_month

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        selected_month = self._selected_month()
        month_range = self._month_range(selected_month)

        activity_filter = models.Q()
        selected_period_label = (
            "Cała historia" if self.request.LANGUAGE_CODE == "pl" else "All history"
        )

        if month_range is not None:
            month_start, next_month = month_range
            activity_filter &= models.Q(
                prospect_activities__activity_date__gte=month_start,
                prospect_activities__activity_date__lt=next_month,
            )
            selected_period_label = month_start.strftime("%Y-%m")

        sellers = list(
            User.objects.filter(account_type=AccountType.STAFF, is_superuser=False)
            .annotate(activity_count=models.Count("prospect_activities", filter=activity_filter))
            .order_by("-activity_count", "username")
        )

        total_activities = sum(seller.activity_count for seller in sellers)
        active_sellers = sum(1 for seller in sellers if seller.activity_count > 0)

        context["report_rows"] = sellers
        context["chart_labels"] = [seller.username for seller in sellers]
        context["chart_values"] = [seller.activity_count for seller in sellers]
        context["selected_month"] = selected_month if month_range is not None else ""
        context["selected_period_label"] = selected_period_label
        context["total_activities"] = total_activities
        context["active_sellers"] = active_sellers
        context["show_all_history"] = self._show_all_history()
        return context


# ===== Seller Workspace Views =====

class SellerRequiredMixin(LoginRequiredMixin):
    """Mixin ensuring user is a seller (STAFF account type)."""
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.user.account_type != AccountType.STAFF:
            return redirect("dashboard:home")
        return super().dispatch(request, *args, **kwargs)


class SellerOrAdminRequiredMixin(LoginRequiredMixin):
    """Allow access for sellers and administrators."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.user.is_superuser or request.user.account_type == AccountType.STAFF:
            return super().dispatch(request, *args, **kwargs)
        return redirect("dashboard:home")


class SellerClientsListView(SellerRequiredMixin, TemplateView):
    """Show registered clients (users who signed up and selected a plan)."""
    template_name = "dashboard/seller_clients.html"

    def get_context_data(self, **kwargs):
        from apps.sales.models import ProspectClient

        context = super().get_context_data(**kwargs)
        # Show all registered clients (not sellers, not superusers)
        linked_prospect_subquery = ProspectClient.objects.filter(
            registered_client=models.OuterRef("pk")
        ).values("pk")[:1]
        linked_seller_subquery = ProspectClient.objects.filter(
            registered_client=models.OuterRef("pk")
        ).values("seller__username")[:1]
        clients = User.objects.filter(
            is_superuser=False,
            account_type=AccountType.CLIENT
        ).annotate(
            linked_prospect_id=models.Subquery(linked_prospect_subquery),
            linked_seller_username=models.Subquery(linked_seller_subquery)
        ).order_by("email")
        context["clients"] = clients
        return context


class SellerProspectsListView(SellerRequiredMixin, TemplateView):
    """Show prospects with filter: own or all."""
    template_name = "dashboard/seller_prospects.html"

    def get_context_data(self, **kwargs):
        from apps.sales.models import ProspectClient
        
        context = super().get_context_data(**kwargs)
        filter_type = self.request.GET.get("filter", "own")
        
        if filter_type == "all":
            prospects = ProspectClient.objects.select_related("seller").order_by("-created_at")
            context["is_viewing_all"] = True
        else:
            prospects = ProspectClient.objects.filter(
                seller=self.request.user
            ).order_by("-created_at")
            context["is_viewing_all"] = False
        
        # Add last activity to each prospect
        for prospect in prospects:
            prospect.last_activity = prospect.activities.order_by("-activity_date").first()
        
        context["prospects"] = prospects
        context["filter_type"] = filter_type
        return context


class ProspectCreateView(SellerRequiredMixin, FormView):
    """Create a new prospect for the seller."""
    form_class = ProspectClientForm
    template_name = "dashboard/prospect_form.html"
    success_url = reverse_lazy("dashboard:seller-prospects")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["language_code"] = self.request.LANGUAGE_CODE
        return kwargs

    def form_valid(self, form):
        from apps.sales.models import ProspectClient
        
        ProspectClient.objects.create(
            seller=self.request.user,
            company_name=form.cleaned_data["company_name"],
            contact_person=form.cleaned_data["contact_person"],
            email=form.cleaned_data["email"],
            phone=form.cleaned_data["phone"],
            website_url=form.cleaned_data.get("website_url", ""),
            notes=form.cleaned_data.get("notes", ""),
            registered_client=form.cleaned_data.get("registered_client"),
        )
        
        if self.request.LANGUAGE_CODE == "pl":
            messages.success(self.request, "Prospect dodany do listy.")
        else:
            messages.success(self.request, "Prospect added to the list.")
        return super().form_valid(form)


class ProspectUpdateView(SellerRequiredMixin, FormView):
    """Edit an existing prospect."""
    form_class = ProspectClientForm
    template_name = "dashboard/prospect_form.html"

    def get_prospect(self):
        from apps.sales.models import ProspectClient
        return get_object_or_404(ProspectClient, pk=self.kwargs["pk"], seller=self.request.user)

    def get_success_url(self):
        return reverse("dashboard:prospect-detail", kwargs={"pk": self.kwargs["pk"]})

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["language_code"] = self.request.LANGUAGE_CODE
        if self.request.method == "GET":
            prospect = self.get_prospect()
            kwargs["initial"] = {
                "company_name": prospect.company_name,
                "contact_person": prospect.contact_person,
                "email": prospect.email,
                "phone": prospect.phone,
                "website_url": prospect.website_url,
                "notes": prospect.notes,
                "registered_client": prospect.registered_client_id,
            }
        return kwargs

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        prospect = self.get_prospect()
        if prospect.registered_client_id:
            form.fields["registered_client"].queryset = (
                User.objects.filter(
                    models.Q(
                        account_type=AccountType.CLIENT,
                        is_superuser=False,
                        attributed_prospect__isnull=True,
                    ) | models.Q(pk=prospect.registered_client_id)
                ).order_by("email")
            )
        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["is_edit"] = True
        context["prospect"] = self.get_prospect()
        return context

    def form_valid(self, form):
        prospect = self.get_prospect()
        prospect.company_name = form.cleaned_data["company_name"]
        prospect.contact_person = form.cleaned_data["contact_person"]
        prospect.email = form.cleaned_data["email"]
        prospect.phone = form.cleaned_data["phone"]
        prospect.website_url = form.cleaned_data.get("website_url", "")
        prospect.notes = form.cleaned_data.get("notes", "")
        prospect.registered_client = form.cleaned_data.get("registered_client")
        prospect.save()
        if self.request.LANGUAGE_CODE == "pl":
            messages.success(self.request, "Dane prospektu zostały zaktualizowane.")
        else:
            messages.success(self.request, "Prospect updated successfully.")
        return super().form_valid(form)


class ProspectDetailView(SellerOrAdminRequiredMixin, TemplateView):
    """Show prospect details and activities."""
    template_name = "dashboard/prospect_detail.html"

    def get_context_data(self, **kwargs):
        from apps.sales.models import ProspectClient
        
        context = super().get_context_data(**kwargs)
        prospect = get_object_or_404(ProspectClient, pk=self.kwargs["pk"])
        
        # Check if seller can view this prospect (own or all?)
        if prospect.seller != self.request.user:
            # For now, allow viewing others' prospects as per requirement
            pass
        
        can_link_client = prospect.seller == self.request.user
        context["prospect"] = prospect
        context["activities"] = prospect.activities.order_by("-activity_date", "-created_at")
        context["can_link_client"] = can_link_client
        context["link_client_form"] = ProspectLinkClientForm(
            language_code=self.request.LANGUAGE_CODE,
            prospect=prospect,
        )
        return context


class ProspectLinkClientView(SellerRequiredMixin, View):
    """Link seller's prospect with an already registered client account."""

    def post(self, request, pk, *args, **kwargs):
        from apps.sales.models import ProspectClient

        prospect = get_object_or_404(ProspectClient, pk=pk, seller=request.user)
        form = ProspectLinkClientForm(
            request.POST,
            language_code=request.LANGUAGE_CODE,
            prospect=prospect,
        )

        if not form.is_valid():
            if request.LANGUAGE_CODE == "pl":
                messages.error(request, "Nie udało się powiązać prospektu z klientem.")
            else:
                messages.error(request, "Could not link prospect with registered client.")
            return redirect("dashboard:prospect-detail", pk=prospect.pk)

        prospect.registered_client = form.cleaned_data["registered_client"]
        prospect.save()

        if request.LANGUAGE_CODE == "pl":
            messages.success(request, "Prospekt został powiązany z zarejestrowanym klientem.")
        else:
            messages.success(request, "Prospect has been linked with registered client.")

        return redirect("dashboard:prospect-detail", pk=prospect.pk)


class ProspectActivityAddView(SellerOrAdminRequiredMixin, FormView):
    """Add activity to prospect."""
    form_class = ProspectActivityForm
    template_name = "dashboard/prospect_activity_form.html"

    def get_prospect(self):
        from apps.sales.models import ProspectClient

        filters = {"pk": self.kwargs["prospect_pk"]}
        if not self.request.user.is_superuser:
            filters["seller"] = self.request.user
        return get_object_or_404(ProspectClient, **filters)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["language_code"] = self.request.LANGUAGE_CODE
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs, **kwargs)
        context["prospect"] = self.get_prospect()
        return context

    def form_valid(self, form):
        from apps.sales.models import ProspectActivity

        prospect = self.get_prospect()
        
        ProspectActivity.objects.create(
            prospect=prospect,
            seller=self.request.user,
            activity_type=form.cleaned_data["activity_type"],
            activity_date=form.cleaned_data["activity_date"],
            activity_description=form.cleaned_data["activity_description"],
        )
        
        if self.request.LANGUAGE_CODE == "pl":
            messages.success(self.request, "Aktywność dodana.")
        else:
            messages.success(self.request, "Activity added.")
        return redirect("dashboard:prospect-detail", pk=prospect.pk)

