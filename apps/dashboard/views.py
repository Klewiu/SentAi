from datetime import date

import stripe
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, FormView, TemplateView, UpdateView
from django.db import models
from django.utils import timezone

from apps.accounts.models import AccountType, User, UserPlanTier
from apps.companies.forms import OrganizationForm
from apps.companies.models import Organization
from apps.companies.services import public_feed_urls
from apps.subscriptions.models import Subscription

from .forms import SellerCreateForm, UserPlanUpdateForm, ProspectClientForm, ProspectActivityForm
from .forms import ProspectLinkClientForm


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
            self.request.user.paid_plan_started_at = None
            self.request.user.save(update_fields=["plan_tier", "paid_plan_started_at"])
            Subscription.objects.filter(organization__owner=self.request.user).update(tier=selected_tier)
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
            if request.user.paid_plan_started_at is None:
                request.user.paid_plan_started_at = timezone.now()
            request.user.save(update_fields=["plan_tier", "paid_plan_started_at"])
            Subscription.objects.filter(organization__owner=request.user).update(tier=selected_tier)

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


class AdminRequiredMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not request.user.is_superuser:
            return redirect("dashboard:home")
        return super().dispatch(request, *args, **kwargs)


class ClientListView(AdminRequiredMixin, TemplateView):
    template_name = "dashboard/client_list.html"

    def get_context_data(self, **kwargs):
        from apps.sales.models import ProspectClient

        context = super().get_context_data(**kwargs)
        q = self.request.GET.get("q", "").strip()
        linked_prospect_subquery = ProspectClient.objects.filter(
            registered_client=models.OuterRef("pk")
        ).values("pk")[:1]
        linked_seller_subquery = ProspectClient.objects.filter(
            registered_client=models.OuterRef("pk")
        ).values("seller__username")[:1]
        qs = (
            User.objects.filter(is_superuser=False, account_type=AccountType.CLIENT)
            .annotate(linked_prospect_id=models.Subquery(linked_prospect_subquery))
            .annotate(linked_seller_username=models.Subquery(linked_seller_subquery))
            .prefetch_related("organizations")
            .order_by("email")
        )
        if q:
            qs = qs.filter(
                models.Q(company_name__icontains=q)
                | models.Q(email__icontains=q)
                | models.Q(username__icontains=q)
            )
        context["clients"] = qs
        context["search_query"] = q
        return context


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

        context["client"] = client
        context["client_seller"] = seller
        context["client_prospect"] = attributed_prospect
        context["client_phone_number"] = phone_number
        context["client_address"] = ", ".join(part for part in address_parts if part)
        context["organizations"] = [
            {
                "organization": organization,
                "public_urls": public_feed_urls(organization, self.request),
                "supports_jsonld": organization.supports_advanced_formats,
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
            notes=form.cleaned_data.get("notes", ""),
            registered_client=form.cleaned_data.get("registered_client"),
        )
        
        if self.request.LANGUAGE_CODE == "pl":
            messages.success(self.request, "Prospect dodany do listy.")
        else:
            messages.success(self.request, "Prospect added to the list.")
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

