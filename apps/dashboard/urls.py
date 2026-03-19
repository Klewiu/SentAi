from django.urls import path

from .views import (
    DashboardHomeView,
    PlanCheckoutCancelView,
    PlanCheckoutSuccessView,
    OrganizationCreateView,
    OrganizationDeleteView,
    OrganizationUpdateView,
    PlanUpdateView,
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
]

