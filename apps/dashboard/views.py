import stripe
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, FormView, TemplateView, UpdateView

from apps.accounts.models import UserPlanTier
from apps.companies.forms import OrganizationForm
from apps.companies.models import Organization

from .forms import UserPlanUpdateForm


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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        organizations = self.get_queryset()
        context["organizations"] = organizations
        context["organization_count"] = organizations.count()
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
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["current_organization_count"] = self.request.user.organizations.count()
        context["current_organization_limit"] = self.request.user.organization_limit()
        context["stripe_checkout_enabled"] = bool(settings.STRIPE_SECRET_KEY)
        context["stripe_test_mode"] = settings.STRIPE_SECRET_KEY.startswith("sk_test_")
        context["plus_price_label"] = self._format_price_label(
            settings.STRIPE_PLUS_PRICE_AMOUNT,
            settings.STRIPE_CURRENCY,
        )
        context["pro_price_label"] = self._format_price_label(
            settings.STRIPE_PRO_PRICE_AMOUNT,
            settings.STRIPE_CURRENCY,
        )
        return context

    @staticmethod
    def _format_price_label(unit_amount: int, currency: str) -> str:
        return f"{unit_amount / 100:.2f} {currency.upper()}"

    def _price_for_tier(self, tier: str) -> int:
        if tier == UserPlanTier.PLUS:
            return settings.STRIPE_PLUS_PRICE_AMOUNT
        if tier == UserPlanTier.PRO:
            return settings.STRIPE_PRO_PRICE_AMOUNT
        raise ValueError("Unsupported paid plan tier.")

    def _build_checkout_urls(self) -> tuple[str, str]:
        success_url = (
            f"{settings.SITE_BASE_URL}"
            f"{reverse('dashboard:plan-checkout-success')}?session_id={{CHECKOUT_SESSION_ID}}"
        )
        cancel_url = f"{settings.SITE_BASE_URL}{reverse('dashboard:plan-checkout-cancel')}"
        return success_url, cancel_url

    def _create_checkout_session(self, selected_tier: str):
        stripe.api_key = settings.STRIPE_SECRET_KEY
        success_url, cancel_url = self._build_checkout_urls()

        return stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            customer_email=self.request.user.email or None,
            line_items=[
                {
                    "price_data": {
                        "currency": settings.STRIPE_CURRENCY,
                        "unit_amount": self._price_for_tier(selected_tier),
                        "product_data": {
                            "name": f"SentAi {selected_tier.title()} plan",
                        },
                    },
                    "quantity": 1,
                }
            ],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "user_id": str(self.request.user.pk),
                "plan_tier": selected_tier,
            },
        )

    def form_valid(self, form):
        selected_tier = form.cleaned_data["plan_tier"]

        if self.request.user.plan_tier == selected_tier:
            if self.request.LANGUAGE_CODE == "pl":
                messages.info(self.request, "Wybrany plan jest już aktywny.")
            else:
                messages.info(self.request, "This plan is already active.")
            return super().form_valid(form)

        if selected_tier == UserPlanTier.BASIC:
            self.request.user.plan_tier = selected_tier
            self.request.user.save(update_fields=["plan_tier"])
            if self.request.LANGUAGE_CODE == "pl":
                messages.success(self.request, "Plan został zaktualizowany.")
            else:
                messages.success(self.request, "Plan updated successfully.")
            return super().form_valid(form)

        if selected_tier in self.paid_tiers:
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

            try:
                checkout_session = self._create_checkout_session(selected_tier)
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
        payment_status = getattr(checkout_session, "payment_status", "")

        if session_user_id != str(request.user.pk):
            if request.LANGUAGE_CODE == "pl":
                messages.error(request, "Ta sesja płatności nie należy do Twojego konta.")
            else:
                messages.error(request, "This payment session does not belong to your account.")
            return redirect("dashboard:plan-update")

        if selected_tier not in self.paid_tiers:
            if request.LANGUAGE_CODE == "pl":
                messages.error(request, "Nieprawidłowy plan z płatności Stripe.")
            else:
                messages.error(request, "Invalid plan returned from Stripe payment.")
            return redirect("dashboard:plan-update")

        if payment_status != "paid":
            if request.LANGUAGE_CODE == "pl":
                messages.warning(request, "Płatność nie została jeszcze potwierdzona.")
            else:
                messages.warning(request, "Payment has not been confirmed yet.")
            return redirect("dashboard:plan-update")

        if request.user.plan_tier != selected_tier:
            request.user.plan_tier = selected_tier
            request.user.save(update_fields=["plan_tier"])

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

