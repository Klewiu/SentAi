from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path
from django.http import HttpResponseNotFound
from django.views.i18n import set_language
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

from apps.accounts.views import RegisterView
from apps.companies.views import (
    ApiGuideTextView,
    PublicCompanyDetailPageView,
    PublicCompanyDirectoryPageView,
    RobotsTextView,
    SitemapXmlView,
    SiteLLMsTextView,
)
from apps.dashboard.views import LandingView, StripeWebhookView

admin.site.site_header = "SentAi Administration"
admin.site.site_title = "SentAi Admin"
admin.site.index_title = "Platform management"

urlpatterns = [
    re_path(r"^media/invoices(?:/|$)", lambda request: HttpResponseNotFound()),
    path("i18n/", include("django.conf.urls.i18n")),
    # OpenAPI schema + interactive docs (GET only by design)
    path("openapi.json", SpectacularAPIView.as_view(), name="openapi-schema"),
    path("api/schema/swagger-ui/", SpectacularSwaggerView.as_view(url_name="openapi-schema"), name="api-schema-swagger-ui"),
    path("api/schema/redoc/", SpectacularRedocView.as_view(url_name="openapi-schema"), name="api-schema-redoc"),
    # Plain-text guide for developers, crawlers, and AI agents
    path("api-guide.txt", ApiGuideTextView.as_view(), name="api-guide-txt"),
    # Root robots.txt for search engines and AI crawlers
    path("robots.txt", RobotsTextView.as_view(), name="robots-txt"),
    # Dynamic sitemap for public AI-indexable catalog URLs
    path("sitemap.xml", SitemapXmlView.as_view(), name="sitemap-xml"),
    # Site-wide llms.txt for LLM crawlers
    path("llms.txt", SiteLLMsTextView.as_view(), name="site-llms-txt"),
    path("stripe/webhook/", StripeWebhookView.as_view(), name="stripe-webhook"),
    path(
        "companies/<slug:slug>/<str:content_lang>/",
        PublicCompanyDetailPageView.as_view(),
        name="public-company-language-detail",
    ),
    path(
        "api/auth/",
        include(("apps.accounts.api_urls", "accounts_api"), namespace="accounts_api"),
    ),
    path(
        "api/",
        include(("apps.companies.api_urls", "companies_api"), namespace="companies_api"),
    ),
]

urlpatterns += i18n_patterns(
    path("accounts/register/", RegisterView.as_view(), name="register"),
    path("accounts/", include("django.contrib.auth.urls")),
        path("accounts/", include(("apps.accounts.urls", "accounts"), namespace="accounts")),
    path("set-language/", set_language, name="set_language_localized"),
    path("admin/", admin.site.urls),
    path("", LandingView.as_view(), name="landing"),
    path("companies/", PublicCompanyDirectoryPageView.as_view(), name="public-company-directory"),
    path("companies/<slug:slug>/", PublicCompanyDetailPageView.as_view(), name="public-company-detail"),
    path("dashboard/", include(("apps.dashboard.urls", "dashboard"), namespace="dashboard")),
    prefix_default_language=False,
)

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
