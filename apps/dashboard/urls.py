from django.urls import path

from .views import (
    ClientDetailView,
    ClientListView,
    DashboardHomeView,
    PlanCheckoutCancelView,
    PlanCheckoutSuccessView,
    OrganizationCreateView,
    OrganizationDeleteView,
    OrganizationUpdateView,
    PlanUpdateView,
    ProspectActivityAddView,
    ProspectCreateView,
    ProspectDetailView,
    SellerAccessToggleView,
    SellerClientsListView,
    SellerDeleteView,
    SellerDetailView,
    SellerListView,
    SellerProspectsListView,
)


app_name = "dashboard"

urlpatterns = [
    path("", DashboardHomeView.as_view(), name="home"),
    path("plan/", PlanUpdateView.as_view(), name="plan-update"),
    path("plan/checkout/success/", PlanCheckoutSuccessView.as_view(), name="plan-checkout-success"),
    path("plan/checkout/cancel/", PlanCheckoutCancelView.as_view(), name="plan-checkout-cancel"),
    path("organizations/new/", OrganizationCreateView.as_view(), name="organization-create"),
    path("organizations/<int:pk>/edit/", OrganizationUpdateView.as_view(), name="organization-edit"),
    path("organizations/<int:pk>/delete/", OrganizationDeleteView.as_view(), name="organization-delete"),
    path("clients/", ClientListView.as_view(), name="client-list"),
    path("clients/<int:pk>/", ClientDetailView.as_view(), name="client-detail"),
    path("sellers/", SellerListView.as_view(), name="seller-list"),
    path("sellers/<int:pk>/", SellerDetailView.as_view(), name="seller-detail"),
    path("sellers/<int:pk>/toggle-access/", SellerAccessToggleView.as_view(), name="seller-toggle-access"),
    path("sellers/<int:pk>/delete/", SellerDeleteView.as_view(), name="seller-delete"),
    # Seller workspace routes
    path("seller/clients/", SellerClientsListView.as_view(), name="seller-clients"),
    path("seller/prospects/", SellerProspectsListView.as_view(), name="seller-prospects"),
    path("seller/prospects/new/", ProspectCreateView.as_view(), name="prospect-create"),
    path("seller/prospects/<int:pk>/", ProspectDetailView.as_view(), name="prospect-detail"),
    path("seller/prospects/<int:prospect_pk>/activity/", ProspectActivityAddView.as_view(), name="prospect-activity-add"),
]

