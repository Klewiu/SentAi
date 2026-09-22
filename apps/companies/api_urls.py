from django.urls import path

from .views import (
    BulkAllJsonView,
    CatalogNdjsonView,
    CompanyDetailView,
    CompanyListView,
    CompanyUpdatesView,
    ContentEntryDetailView,
    ContentEntryListCreateView,
    OrganizationDetailView,
    OrganizationListCreateView,
    ProductDetailView,
    ProductListCreateView,
    PublicCompanyJsonLdView,
    PublicCompanyJsonView,
    PublicCompanyLocalizedJsonView,
    PublicCompanyLocalizedJsonLdView,
    PublicCompanyLocalizedMarkdownView,
    PublicCompanyLocalizedLLMsTextView,
    PublicCompanyMarkdownView,
    PublicLLMsTextView,
    SocialProfileDetailView,
    SocialProfileListCreateView,
    TagDetailView,
    TagListCreateView,
)


app_name = "companies_api"

urlpatterns = [
    # -----------------------------------------------------------------
    # Private management endpoints — authenticated, owner-scoped
    # -----------------------------------------------------------------
    path("organizations/", OrganizationListCreateView.as_view(), name="organization-list"),
    path("organizations/<int:pk>/", OrganizationDetailView.as_view(), name="organization-detail"),
    path(
        "organizations/<int:organization_pk>/social-profiles/",
        SocialProfileListCreateView.as_view(),
        name="social-profile-list",
    ),
    path(
        "organizations/<int:organization_pk>/social-profiles/<int:pk>/",
        SocialProfileDetailView.as_view(),
        name="social-profile-detail",
    ),
    path(
        "organizations/<int:organization_pk>/tags/",
        TagListCreateView.as_view(),
        name="tag-list",
    ),
    path(
        "organizations/<int:organization_pk>/tags/<int:pk>/",
        TagDetailView.as_view(),
        name="tag-detail",
    ),
    path(
        "organizations/<int:organization_pk>/products/",
        ProductListCreateView.as_view(),
        name="product-list",
    ),
    path(
        "organizations/<int:organization_pk>/products/<int:pk>/",
        ProductDetailView.as_view(),
        name="product-detail",
    ),
    path(
        "organizations/<int:organization_pk>/entries/",
        ContentEntryListCreateView.as_view(),
        name="entry-list",
    ),
    path(
        "organizations/<int:organization_pk>/entries/<int:pk>/",
        ContentEntryDetailView.as_view(),
        name="entry-detail",
    ),
    # -----------------------------------------------------------------
    # Public catalog — read-only, no authentication required
    # -----------------------------------------------------------------
    path("companies/", CompanyListView.as_view(), name="company-list"),
    # updates/ must come before <slug:slug>/ to avoid slug capturing "updates"
    path("companies/updates/", CompanyUpdatesView.as_view(), name="company-updates"),
    path("companies/<slug:slug>/", CompanyDetailView.as_view(), name="company-detail"),
    # -----------------------------------------------------------------
    # Bulk access
    # -----------------------------------------------------------------
    path("public/all.json", BulkAllJsonView.as_view(), name="public-all-json"),
    path("public/catalog.ndjson", CatalogNdjsonView.as_view(), name="public-catalog-ndjson"),
    # -----------------------------------------------------------------
    # Per-company structured formats
    # -----------------------------------------------------------------
    path("public/<slug:slug>/company.json", PublicCompanyJsonView.as_view(), name="public-company-json"),
    path("public/<slug:slug>/company.jsonld", PublicCompanyJsonLdView.as_view(), name="public-company-jsonld"),
    path("public/<slug:slug>/company.md", PublicCompanyMarkdownView.as_view(), name="public-company-md"),
    path("public/<slug:slug>/llms.txt", PublicLLMsTextView.as_view(), name="public-company-llms"),
    path("public/<slug:slug>/<str:language_code>/company.json", PublicCompanyLocalizedJsonView.as_view(), name="public-company-language-json"),
    path("public/<slug:slug>/<str:language_code>/company.jsonld", PublicCompanyLocalizedJsonLdView.as_view(), name="public-company-language-jsonld"),
    path("public/<slug:slug>/<str:language_code>/company.md", PublicCompanyLocalizedMarkdownView.as_view(), name="public-company-language-md"),
    path("public/<slug:slug>/<str:language_code>/llms.txt", PublicCompanyLocalizedLLMsTextView.as_view(), name="public-company-language-llms"),
]

