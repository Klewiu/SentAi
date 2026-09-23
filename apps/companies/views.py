import datetime
import hashlib
import json
from xml.sax.saxutils import escape

from django.conf import settings
from django.http import Http404, HttpResponse, StreamingHttpResponse
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, TextField
from django.db.models.functions import Cast
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_aware, make_aware
from django.views.generic import TemplateView
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters as drf_filters
from rest_framework import generics, permissions
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
import django_filters

from .models import ContentEntry, Organization, Product, SocialProfile, Tag, VerificationStatus
from .permissions import IsOrganizationOwnerOrAdmin
from .serializers import (
    ContentEntrySerializer,
    OrganizationSerializer,
    ProductSerializer,
    SocialProfileSerializer,
    TagSerializer,
)
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema

from .services import (
    build_basic_feed,
    build_jsonld_feed,
    build_llms_text,
    build_localized_feed,
    build_markdown_feed,
    primary_public_language,
    profile_language_choices,
    public_feed_urls,
    public_profile_url,
    public_resources,
)


PROFILE_COPY = {
    "en": {"profile_suffix": "company profile", "companies": "companies", "profile_language": "Profile language", "company_facts": "Company facts", "type": "type", "language": "language", "location": "location", "verification": "verification", "last_updated": "last updated", "website": "website", "products": "Products and services", "price_from": "from", "source": "source", "content": "FAQs and company content", "topics": "Specialties", "formats": "AI-ready formats", "formats_intro": "These formats help AI systems, search engines and digital assistants recognize your company and use its information more accurately.", "social": "Social profiles", "verified": "Verified by an administrator"},
    "pl": {"profile_suffix": "profil firmy", "companies": "firmy", "profile_language": "Język profilu", "company_facts": "Informacje o firmie", "type": "typ", "language": "język", "location": "lokalizacja", "verification": "weryfikacja", "last_updated": "ostatnia aktualizacja", "website": "strona internetowa", "products": "Produkty i usługi", "price_from": "od", "source": "źródło", "content": "FAQ i materiały firmy", "topics": "Specjalizacje", "formats": "Formaty gotowe dla AI", "formats_intro": "Dzięki tym formatom systemy AI, wyszukiwarki i cyfrowi asystenci mogą łatwiej rozpoznać Twoją firmę i poprawnie korzystać z jej informacji.", "social": "Profile społecznościowe", "verified": "Zweryfikowano przez administratora"},
    "de": {"profile_suffix": "Unternehmensprofil", "companies": "Unternehmen", "profile_language": "Profilsprache", "company_facts": "Unternehmensdaten", "type": "Typ", "language": "Sprache", "location": "Standort", "verification": "Verifizierung", "last_updated": "zuletzt aktualisiert", "website": "Website", "products": "Produkte und Dienstleistungen", "price_from": "ab", "source": "Quelle", "content": "FAQ und Unternehmensinhalte", "topics": "Fachgebiete", "formats": "Maschinenlesbare Formate", "social": "Soziale Profile", "verified": "Durch einen Administrator verifiziert"},
    "es": {"profile_suffix": "perfil de empresa", "companies": "empresas", "profile_language": "Idioma del perfil", "company_facts": "Datos de la empresa", "type": "tipo", "language": "idioma", "location": "ubicación", "verification": "verificación", "last_updated": "última actualización", "website": "sitio web", "products": "Productos y servicios", "price_from": "desde", "source": "fuente", "content": "Preguntas frecuentes y contenido", "topics": "Especialidades", "formats": "Formatos legibles por máquina", "social": "Perfiles sociales", "verified": "Verificado por un administrador"},
    "it": {"profile_suffix": "profilo aziendale", "companies": "aziende", "profile_language": "Lingua del profilo", "company_facts": "Dati aziendali", "type": "tipo", "language": "lingua", "location": "sede", "verification": "verifica", "last_updated": "ultimo aggiornamento", "website": "sito web", "products": "Prodotti e servizi", "price_from": "da", "source": "fonte", "content": "FAQ e contenuti aziendali", "topics": "Specializzazioni", "formats": "Formati leggibili dalle macchine", "social": "Profili social", "verified": "Verificato da un amministratore"},
    "fr": {"profile_suffix": "profil d’entreprise", "companies": "entreprises", "profile_language": "Langue du profil", "company_facts": "Informations sur l’entreprise", "type": "type", "language": "langue", "location": "localisation", "verification": "vérification", "last_updated": "dernière mise à jour", "website": "site web", "products": "Produits et services", "price_from": "à partir de", "source": "source", "content": "FAQ et contenus de l’entreprise", "topics": "Spécialités", "formats": "Formats lisibles par machine", "social": "Profils sociaux", "verified": "Vérifié par un administrateur"},
}

COMPANY_TYPE_COPY = {
    "en": {"manufacturing": "Manufacturing", "services": "Services", "trading": "Trading", "other": "Other"},
    "pl": {"manufacturing": "Produkcja", "services": "Usługi", "trading": "Handel", "other": "Inne"},
    "de": {"manufacturing": "Produktion", "services": "Dienstleistungen", "trading": "Handel", "other": "Sonstiges"},
    "es": {"manufacturing": "Fabricación", "services": "Servicios", "trading": "Comercio", "other": "Otro"},
    "it": {"manufacturing": "Produzione", "services": "Servizi", "trading": "Commercio", "other": "Altro"},
    "fr": {"manufacturing": "Production", "services": "Services", "trading": "Commerce", "other": "Autre"},
}

PUBLIC_LANGUAGE_PARAMETER = OpenApiParameter(
    name="language_code",
    type=str,
    location=OpenApiParameter.PATH,
    required=True,
    enum=[code for code, _label in getattr(settings, "FEED_LANGUAGES", settings.LANGUAGES)],
    description=(
        "Published profile language. The language must be enabled for this company and "
        "available under its plan; otherwise the endpoint returns 404."
    ),
)


def verified_public_organization_queryset():
    from apps.billing.access import publication_users
    return Organization.objects.filter(
        owner__in=publication_users(),
        public=True,
        allow_ai_indexing=True,
        verification_status=VerificationStatus.HUMAN_ADMIN_VERIFIED,
    )


def filter_organizations_by_text(queryset, value):
    """Search visible text and every stored translation without assuming a fixed language list."""
    if not value:
        return queryset
    queryset = queryset.annotate(
        search_descriptions=Cast("descriptions_by_language", TextField()),
        search_product_names=Cast("products__names_by_language", TextField()),
        search_product_descriptions=Cast("products__descriptions_by_language", TextField()),
        search_questions=Cast("content_entries__questions_by_language", TextField()),
        search_answers=Cast("content_entries__answers_by_language", TextField()),
    )
    search_query = (
        Q(name__icontains=value)
        | Q(ai_summary__icontains=value)
        | Q(search_descriptions__icontains=value)
        | Q(short_description_en__icontains=value)
        | Q(short_description_pl__icontains=value)
        | Q(long_description_en__icontains=value)
        | Q(long_description_pl__icontains=value)
        | Q(tags__name__icontains=value)
        | Q(products__name__icontains=value)
        | Q(products__short_description_en__icontains=value)
        | Q(products__short_description_pl__icontains=value)
        | Q(search_product_names__icontains=value)
        | Q(search_product_descriptions__icontains=value)
        | Q(content_entries__title__icontains=value)
        | Q(content_entries__summary_en__icontains=value)
        | Q(content_entries__summary_pl__icontains=value)
        | Q(search_questions__icontains=value)
        | Q(search_answers__icontains=value)
    )
    for language_code, _label in getattr(settings, "FEED_LANGUAGES", settings.LANGUAGES):
        for field in ("short", "long"):
            search_query |= Q(**{
                f"descriptions_by_language__{language_code}__{field}__icontains": value
            })
        search_query |= Q(**{
            f"products__names_by_language__{language_code}__icontains": value
        })
        search_query |= Q(**{
            f"products__descriptions_by_language__{language_code}__icontains": value
        })
        search_query |= Q(**{
            f"content_entries__questions_by_language__{language_code}__icontains": value
        })
        search_query |= Q(**{
            f"content_entries__answers_by_language__{language_code}__icontains": value
        })
    return queryset.filter(search_query).distinct()


class OwnedOrganizationQuerysetMixin:
    def get_organization_queryset(self):
        queryset = Organization.objects.select_related("owner", "subscription")
        if self.request.user.is_superuser:
            return queryset
        return queryset.filter(owner=self.request.user)


@extend_schema(exclude=True)
class OrganizationListCreateView(OwnedOrganizationQuerysetMixin, generics.ListCreateAPIView):
    serializer_class = OrganizationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return self.get_organization_queryset()

    @transaction.atomic
    def perform_create(self, serializer):
        from apps.accounts.models import User
        self.request.user = User.objects.select_for_update().get(pk=self.request.user.pk)
        if not self.request.user.is_superuser:
            current_count = self.get_organization_queryset().count()
            if not self.request.user.can_add_organization(current_count):
                limit = self.request.user.organization_limit()
                raise ValidationError(
                    {"plan": f"The {self.request.user.plan_tier} plan allows up to {limit} company pages."}
                )
        serializer.save(owner=self.request.user)


@extend_schema(exclude=True)
class OrganizationDetailView(OwnedOrganizationQuerysetMixin, generics.RetrieveUpdateAPIView):
    serializer_class = OrganizationSerializer
    permission_classes = [permissions.IsAuthenticated, IsOrganizationOwnerOrAdmin]

    def get_queryset(self):
        return self.get_organization_queryset()

    @transaction.atomic
    def perform_update(self, serializer):
        from apps.accounts.models import User
        User.objects.select_for_update().get(pk=serializer.instance.owner_id)
        serializer.instance = Organization.objects.select_for_update().get(pk=serializer.instance.pk)
        serializer.validated_data.update(serializer.validate(serializer.validated_data))
        serializer.save()


class OrganizationResourceMixin(OwnedOrganizationQuerysetMixin):
    relation_name = ""

    def get_organization(self):
        if not hasattr(self, "_organization"):
            self._organization = get_object_or_404(
                self.get_organization_queryset(),
                pk=self.kwargs["organization_pk"],
            )
        return self._organization

    def get_queryset(self):
        return getattr(self.get_organization(), self.relation_name).all()

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["organization"] = self.get_organization()
        return context

    @transaction.atomic
    def perform_create(self, serializer):
        Organization.objects.select_for_update().get(pk=self.get_organization().pk)
        serializer.validate(serializer.validated_data)
        serializer.save(organization=self.get_organization())

    @transaction.atomic
    def perform_update(self, serializer):
        Organization.objects.select_for_update().get(pk=self.get_organization().pk)
        serializer.validate(serializer.validated_data)
        serializer.save()


@extend_schema(exclude=True)
class SocialProfileListCreateView(OrganizationResourceMixin, generics.ListCreateAPIView):
    serializer_class = SocialProfileSerializer
    permission_classes = [permissions.IsAuthenticated]
    relation_name = "social_profiles"


@extend_schema(exclude=True)
class SocialProfileDetailView(OrganizationResourceMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = SocialProfileSerializer
    permission_classes = [permissions.IsAuthenticated, IsOrganizationOwnerOrAdmin]
    relation_name = "social_profiles"


@extend_schema(exclude=True)
class TagListCreateView(OrganizationResourceMixin, generics.ListCreateAPIView):
    serializer_class = TagSerializer
    permission_classes = [permissions.IsAuthenticated]
    relation_name = "tags"


@extend_schema(exclude=True)
class TagDetailView(OrganizationResourceMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = TagSerializer
    permission_classes = [permissions.IsAuthenticated, IsOrganizationOwnerOrAdmin]
    relation_name = "tags"


@extend_schema(exclude=True)
class ProductListCreateView(OrganizationResourceMixin, generics.ListCreateAPIView):
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticated]
    relation_name = "products"


@extend_schema(exclude=True)
class ProductDetailView(OrganizationResourceMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticated, IsOrganizationOwnerOrAdmin]
    relation_name = "products"


@extend_schema(exclude=True)
class ContentEntryListCreateView(OrganizationResourceMixin, generics.ListCreateAPIView):
    serializer_class = ContentEntrySerializer
    permission_classes = [permissions.IsAuthenticated]
    relation_name = "content_entries"


@extend_schema(exclude=True)
class ContentEntryDetailView(OrganizationResourceMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ContentEntrySerializer
    permission_classes = [permissions.IsAuthenticated, IsOrganizationOwnerOrAdmin]
    relation_name = "content_entries"


class PublicOrganizationMixin:
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get_organization(self):
        queryset = verified_public_organization_queryset().select_related("subscription").prefetch_related(
            "social_profiles",
            "tags",
            "products",
            "content_entries",
        )
        return get_object_or_404(
            queryset,
            slug=self.kwargs["slug"],
        )

    def get_public_language(self, organization, *, default_to_primary=False):
        language_code = self.kwargs.get("language_code")
        if language_code is None and default_to_primary:
            language_code = primary_public_language(organization)
        if language_code is not None:
            available = {item["code"] for item in profile_language_choices(organization)}
            if language_code not in available:
                raise Http404()
        return language_code

    def add_language_headers(self, response, organization, language_code, format_key):
        if not language_code:
            return response
        response["Content-Language"] = language_code
        alternates = []
        for item in profile_language_choices(organization):
            url = public_feed_urls(organization, self.request, item["code"])[format_key]
            alternates.append(f'<{url}>; rel="alternate"; hreflang="{item["code"]}"')
        canonical = public_feed_urls(organization, self.request, language_code)[format_key]
        default_url = public_feed_urls(
            organization,
            self.request,
            primary_public_language(organization),
        )[format_key]
        response["Link"] = ", ".join(
            [f'<{canonical}>; rel="canonical"']
            + alternates
            + [f'<{default_url}>; rel="alternate"; hreflang="x-default"']
        )
        return response


class PublicCompanyDirectoryPageView(TemplateView):
    template_name = "companies/directory.html"

    def get_queryset(self):
        queryset = (
            verified_public_organization_queryset()
            .select_related("subscription", "owner")
            .prefetch_related("tags")
            .order_by("name")
        )
        query = self.request.GET.get("q", "").strip()
        if query:
            queryset = filter_organizations_by_text(queryset, query)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        paginator = Paginator(self.get_queryset(), 24)
        page_obj = paginator.get_page(self.request.GET.get("page"))
        context["page_obj"] = page_obj
        context["organizations"] = page_obj.object_list
        context["query"] = self.request.GET.get("q", "").strip()
        context["total_count"] = paginator.count
        context["canonical_url"] = self.request.build_absolute_uri(reverse("public-company-directory"))
        context["directory_feed_urls"] = {
            "all_json": self.request.build_absolute_uri(reverse("companies_api:public-all-json")),
            "catalog_ndjson": self.request.build_absolute_uri(reverse("companies_api:public-catalog-ndjson")),
            "llms_txt": self.request.build_absolute_uri(reverse("site-llms-txt")),
            "openapi": self.request.build_absolute_uri(reverse("openapi-schema")),
        }
        return context


class PublicCompanyDetailPageView(TemplateView):
    template_name = "companies/detail.html"

    def get_organization(self):
        if not hasattr(self, "_organization"):
            self._organization = get_object_or_404(
                verified_public_organization_queryset()
                .select_related("subscription", "owner")
                .prefetch_related("social_profiles", "tags", "products", "content_entries"),
                slug=self.kwargs["slug"],
            )
        return self._organization

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        organization = self.get_organization()
        context["organization"] = organization
        from .services import _description_payload
        descriptions = _description_payload(organization)
        choices = profile_language_choices(organization)
        selected_language = self.kwargs.get("content_lang") or self.request.GET.get("content_lang") or primary_public_language(organization)
        available_codes = [item["code"] for item in choices]
        if selected_language not in available_codes:
            raise Http404()
        content_language = selected_language
        alternate_urls = [
            {**item, "url": public_profile_url(organization, item["code"], self.request)}
            for item in choices
        ]
        context["profile_languages"] = alternate_urls
        selected_description = descriptions.get(content_language, {})
        description = selected_description.get("long") or selected_description.get("short") or organization.ai_summary
        context["content_language"] = content_language
        context["document_language"] = content_language
        interface_language = self.request.COOKIES.get(
            settings.LANGUAGE_COOKIE_NAME,
            self.request.LANGUAGE_CODE,
        )
        if interface_language not in {"en", "pl"}:
            interface_language = "en"
        self.request.LANGUAGE_CODE = interface_language
        context["profile_copy"] = PROFILE_COPY[interface_language]
        context["language_fallback"] = False
        context["company_type_label"] = COMPANY_TYPE_COPY[interface_language].get(organization.company_type, organization.get_company_type_display())
        context["verification_label"] = context["profile_copy"]["verified"]
        context["feed_urls"] = public_feed_urls(organization, self.request, content_language)
        context["canonical_url"] = public_profile_url(organization, content_language, self.request)
        context["x_default_url"] = public_profile_url(organization, primary_public_language(organization), self.request)
        context["jsonld_payload"] = ""
        if organization.supports_advanced_formats:
            jsonld = build_jsonld_feed(organization, self.request, language_code=content_language)
            jsonld["description"] = description
            context["jsonld_payload"] = json.dumps(
                jsonld,
                ensure_ascii=False,
            ).replace("<", "\\u003C").replace(">", "\\u003E").replace("&", "\\u0026")
        context["description"] = description
        context["short_description"] = selected_description.get("short", "")
        context["long_description"] = selected_description.get("long", "")
        context["products"] = [
            product for product in public_resources(organization, "products")
            if product.translation_in(content_language)
        ]
        context["entries"] = [
            entry for entry in public_resources(organization, "content_entries")
            if entry.translation_in(content_language)
        ]
        for product in context["products"]:
            translation = product.translation_in(content_language)
            product.display_name = translation["name"]
            product.display_summary = translation["description"]
        for entry in context["entries"]:
            translation = entry.translation_in(content_language)
            entry.display_title = translation["question"]
            entry.display_summary = translation["answer"]
        context["tags"] = [
            tag for tag in public_resources(organization, "tags")
            if not tag.language or tag.language == content_language
        ]
        context["social_profiles"] = public_resources(organization, "social_profiles")
        return context

    def render_to_response(self, context, **response_kwargs):
        response = super().render_to_response(context, **response_kwargs)
        response["Content-Language"] = context["content_language"]
        response["Link"] = ", ".join(
            [f'<{context["canonical_url"]}>; rel="canonical"']
            + [f'<{item["url"]}>; rel="alternate"; hreflang="{item["code"]}"' for item in context["profile_languages"]]
            + [f'<{context["x_default_url"]}>; rel="alternate"; hreflang="x-default"']
        )
        return response


class PublicCompanyJsonView(PublicOrganizationMixin, APIView):
    @extend_schema(
        operation_id="public_company_json_retrieve",
        description="Return the multilingual master profile with all published translations and localized URLs.",
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request, *args, **kwargs):
        organization = self.get_organization()
        language_code = self.get_public_language(organization)
        response = Response(
            build_localized_feed(organization, language_code, request)
            if language_code else build_basic_feed(organization, request)
        )
        return self.add_language_headers(response, organization, language_code, "company_json")


class PublicCompanyJsonLdView(PublicOrganizationMixin, APIView):
    @extend_schema(
        operation_id="public_company_jsonld_retrieve",
        description="Return Schema.org JSON-LD in the company's primary published language.",
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request, *args, **kwargs):
        organization = self.get_organization()
        if not organization.get_subscription().supports("advanced_formats"):
            raise Http404()
        language_code = self.get_public_language(organization, default_to_primary=True)
        response = Response(build_jsonld_feed(organization, request, language_code), content_type="application/ld+json")
        return self.add_language_headers(response, organization, language_code, "company_jsonld")


class PublicLLMsTextView(PublicOrganizationMixin, APIView):
    @extend_schema(
        operation_id="public_company_llms_retrieve",
        description="Return the llms.txt profile in the company's primary published language.",
        responses=OpenApiTypes.STR,
    )
    def get(self, request, *args, **kwargs):
        organization = self.get_organization()
        if not organization.get_subscription().supports("llms_txt"):
            raise Http404()
        language_code = self.get_public_language(organization, default_to_primary=True)
        response = HttpResponse(build_llms_text(organization, request, language_code), content_type="text/plain; charset=utf-8")
        return self.add_language_headers(response, organization, language_code, "llms_txt")


class PublicCompanyMarkdownView(PublicOrganizationMixin, APIView):
    @extend_schema(
        operation_id="public_company_markdown_retrieve",
        description="Return Markdown in the company's primary published language.",
        responses=OpenApiTypes.STR,
    )
    def get(self, request, *args, **kwargs):
        organization = self.get_organization()
        if not organization.get_subscription().supports("company_md"):
            raise Http404()
        language_code = self.get_public_language(organization, default_to_primary=True)
        response = HttpResponse(
            build_markdown_feed(organization, request, language_code),
            content_type="text/markdown; charset=utf-8",
        )
        return self.add_language_headers(response, organization, language_code, "company_md")


class PublicCompanyLocalizedJsonView(PublicCompanyJsonView):
    @extend_schema(
        operation_id="public_company_localized_json_retrieve",
        description="Return only customer content authored in the requested published language.",
        parameters=[PUBLIC_LANGUAGE_PARAMETER],
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class PublicCompanyLocalizedJsonLdView(PublicCompanyJsonLdView):
    @extend_schema(
        operation_id="public_company_localized_jsonld_retrieve",
        description="Return language-specific Schema.org JSON-LD with canonical and alternate links.",
        parameters=[PUBLIC_LANGUAGE_PARAMETER],
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class PublicCompanyLocalizedMarkdownView(PublicCompanyMarkdownView):
    @extend_schema(
        operation_id="public_company_localized_markdown_retrieve",
        description="Return Markdown containing only customer content authored in the requested language.",
        parameters=[PUBLIC_LANGUAGE_PARAMETER],
        responses=OpenApiTypes.STR,
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class PublicCompanyLocalizedLLMsTextView(PublicLLMsTextView):
    @extend_schema(
        operation_id="public_company_localized_llms_retrieve",
        description="Return an llms.txt profile containing only customer content authored in the requested language.",
        parameters=[PUBLIC_LANGUAGE_PARAMETER],
        responses=OpenApiTypes.STR,
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


# ---------------------------------------------------------------------------
# Public catalog — no authentication required
# ---------------------------------------------------------------------------

def _catalog_entry(org, request) -> dict:
    sub = org.get_subscription()
    return {
        "id": str(org.public_id),
        "name": org.name,
        "slug": org.slug,
        "company_type": org.company_type,
        "company_type_label": org.get_company_type_display(),
        "city": org.city,
        "country": org.country,
        "primary_language": org.primary_language,
        "profile_languages": [item["code"] for item in profile_language_choices(org)],
        "profile_urls": {
            item["code"]: public_profile_url(org, item["code"], request)
            for item in profile_language_choices(org)
        },
        "localized_feed_urls": {
            item["code"]: public_feed_urls(org, request, item["code"])
            for item in profile_language_choices(org)
        },
        "website_url": org.website_url,
        "ai_summary": org.ai_summary,
        "tags": [tag.name for tag in public_resources(org, "tags")],
        "verification_status": org.verification_status,
        "verified_at": org.verified_at.isoformat() if org.verified_at else None,
        "last_reviewed_at": org.last_reviewed_at.isoformat() if org.last_reviewed_at else None,
        "available_formats": {
            "company_json": True,
            "company_jsonld": sub.supports("advanced_formats"),
            "company_md": sub.supports("company_md"),
            "llms_txt": sub.supports("llms_txt"),
        },
        "feed_urls": public_feed_urls(org, request),
        "updated_at": org.updated_at.isoformat(),
    }


class CompanyFilter(django_filters.FilterSet):
    search = django_filters.CharFilter(method="filter_search", label="Search name or description")
    city = django_filters.CharFilter(field_name="city", lookup_expr="icontains")
    country = django_filters.CharFilter(field_name="country", lookup_expr="iexact")
    language = django_filters.CharFilter(field_name="primary_language", lookup_expr="exact")
    company_type = django_filters.CharFilter(field_name="company_type", lookup_expr="exact")
    tag = django_filters.CharFilter(method="filter_tag", label="Filter by tag name")
    verified = django_filters.BooleanFilter(method="filter_verified", label="Verified companies only")
    has_price = django_filters.BooleanFilter(method="filter_has_price", label="Companies with priced products")

    class Meta:
        model = Organization
        fields = ["city", "country", "language", "company_type", "tag", "verified", "has_price"]

    def filter_search(self, queryset, name, value):
        return filter_organizations_by_text(queryset, value)

    def filter_tag(self, queryset, name, value):
        return queryset.filter(tags__name__icontains=value).distinct()

    def filter_verified(self, queryset, name, value):
        from .models import VerificationStatus
        if value:
            return queryset.filter(verification_status=VerificationStatus.HUMAN_ADMIN_VERIFIED)
        return queryset

    def filter_has_price(self, queryset, name, value):
        if value:
            return queryset.filter(products__price_from__isnull=False).distinct()
        return queryset


class CompanyListView(generics.GenericAPIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    filterset_class = CompanyFilter
    filter_backends = [DjangoFilterBackend, drf_filters.OrderingFilter]
    ordering_fields = ["name", "updated_at", "created_at"]
    ordering = ["name"]

    def get_queryset(self):
        return (
            verified_public_organization_queryset()
            .select_related("subscription", "owner")
            .prefetch_related("tags")
            .order_by("name")
        )

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        orgs = page if page is not None else queryset
        results = [_catalog_entry(org, request) for org in orgs]
        if page is not None:
            return self.get_paginated_response(results)
        return Response(results)


class CompanyDetailView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    @extend_schema(operation_id="public_company_catalog_detail", responses=OpenApiTypes.OBJECT)
    def get(self, request, slug, *args, **kwargs):
        org = get_object_or_404(
            verified_public_organization_queryset().select_related("subscription", "owner").prefetch_related(
                "social_profiles", "tags", "products", "content_entries"
            ),
            slug=slug,
        )
        payload = build_basic_feed(org, request)
        etag = '"' + hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest() + '"'
        from django.utils.http import parse_etags
        matches = parse_etags(request.headers.get("If-None-Match", ""))
        unchanged = "*" in matches or etag in [value.removeprefix("W/") for value in matches]
        response = Response(status=304) if unchanged else Response(payload)
        response["Last-Modified"] = org.updated_at.strftime("%a, %d %b %Y %H:%M:%S GMT")
        response["ETag"] = etag
        response["Cache-Control"] = "public, max-age=0, must-revalidate"
        return response


class CompanyUpdatesView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request, *args, **kwargs):
        from django.utils import timezone as tz
        since_str = request.GET.get("since", "").strip()
        if not since_str:
            since = tz.now() - datetime.timedelta(days=7)
            since_str = since.isoformat()
        else:
            since = parse_datetime(since_str)
            if since is None:
                return Response(
                    {"error": "Invalid datetime format. Use ISO 8601, e.g. 2026-01-01T00:00:00Z"},
                    status=400,
                )
            if not is_aware(since):
                since = make_aware(since, datetime.timezone.utc)
        organizations = (
            verified_public_organization_queryset().filter(
                updated_at__gte=since,
            )
            .select_related("subscription", "owner")
            .prefetch_related("tags")
            .order_by("-updated_at", "pk")
        )
        page = Paginator(organizations, 100).get_page(request.GET.get("page", 1))
        results = [_catalog_entry(org, request) for org in page]
        return Response({
            "since": since_str,
            "total": page.paginator.count,
            "page": page.number,
            "next_page": page.next_page_number() if page.has_next() else None,
            "results": results,
        })


class BulkAllJsonView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request, *args, **kwargs):
        from django.utils import timezone
        organizations = (
            verified_public_organization_queryset()
            .select_related("subscription", "owner")
            .prefetch_related("tags")
            .order_by("name")
        )
        latest = (
            verified_public_organization_queryset()
            .order_by("-updated_at")
            .values_list("updated_at", flat=True)
            .first()
        )
        meta = {
                "catalog": "sentai-company-catalog",
                "version": "1.0",
                "generated_at": timezone.now().isoformat(),
                "last_updated": latest.isoformat() if latest else None,
                "total": organizations.count(),
        }
        def generate():
            yield '{"meta":' + json.dumps(meta) + ',"results":['
            first = True
            for org in organizations.iterator(chunk_size=100):
                if not first:
                    yield ","
                yield json.dumps(_catalog_entry(org, request), ensure_ascii=False)
                first = False
            yield "]}"
        return StreamingHttpResponse(generate(), content_type="application/json; charset=utf-8")


class CatalogNdjsonView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    @extend_schema(responses=OpenApiTypes.STR)
    def get(self, request, *args, **kwargs):
        organizations = (
            verified_public_organization_queryset()
            .select_related("subscription", "owner")
            .prefetch_related("social_profiles", "tags", "products", "content_entries")
            .order_by("name")
        )

        def generate():
            for org in organizations.iterator(chunk_size=100):
                yield json.dumps(build_basic_feed(org, request), ensure_ascii=False) + "\n"

        return StreamingHttpResponse(generate(), content_type="application/x-ndjson; charset=utf-8")


class SiteLLMsTextView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    @extend_schema(responses=OpenApiTypes.STR)
    def get(self, request, *args, **kwargs):
        organizations = (
            verified_public_organization_queryset()
            .select_related("subscription", "owner")
            .order_by("name")
        )
        lines = [
            "# llms.txt — SentAi Company Catalog",
            "# This file lists verified companies that have opted in to AI indexing, on every plan.",
            "# Each entry links to a dedicated llms.txt feed for that company.",
            "# Convention: https://llmstxt.org",
            "",
            f"# Companies API: {request.build_absolute_uri(reverse('companies_api:company-list'))}",
            f"# API guide: {request.build_absolute_uri('/api-guide.txt')}",
            f"# OpenAPI schema: {request.build_absolute_uri('/openapi.json')}",
            "",
        ]
        for org in organizations:
            if not org.get_subscription().supports("llms_txt"):
                continue
            lines.append(f"## {org.name}")
            if org.ai_summary:
                lines.append(f"> {org.ai_summary}")
            lines.append(f"- Multilingual JSON: {public_feed_urls(org, request)['company_json']}")
            for language in profile_language_choices(org):
                code = language["code"]
                urls = public_feed_urls(org, request, code)
                lines.append(f"- {code} profile: {public_profile_url(org, code, request)}")
                lines.append(f"- {code} llms.txt: {urls['llms_txt']}")
            lines.append("")
        return HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")


@extend_schema(exclude=True)
class ApiGuideTextView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request, *args, **kwargs):
        guide_path = settings.BASE_DIR / "api-guide.txt"
        if not guide_path.exists():
            raise Http404()
        return HttpResponse(
            guide_path.read_text(encoding="utf-8"),
            content_type="text/plain; charset=utf-8",
        )


@extend_schema(exclude=True)
class RobotsTextView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request, *args, **kwargs):
        lines = [
            "# SentAi crawler policy",
            "# Public company catalog, AI-readable feeds, and API docs are crawlable.",
            "# The sitemap lists every published profile language with hreflang alternates.",
            "# The LLM index links to every localized company llms.txt feed.",
            "",
            "User-agent: *",
            "Allow: /",
            "",
            "User-agent: OAI-SearchBot",
            "Allow: /",
            "",
            "User-agent: GPTBot",
            "Allow: /",
            "",
            "User-agent: ChatGPT-User",
            "Allow: /",
            "",
            "User-agent: ClaudeBot",
            "Allow: /",
            "",
            "User-agent: Claude-SearchBot",
            "Allow: /",
            "",
            "User-agent: PerplexityBot",
            "Allow: /",
            "",
            "User-agent: Googlebot",
            "Allow: /",
            "",
            "User-agent: Google-Extended",
            "Allow: /",
            "",
            "User-agent: CCBot",
            "Allow: /",
            "",
            f"# LLM index: {request.build_absolute_uri(reverse('site-llms-txt'))}",
            f"# API guide: {request.build_absolute_uri(reverse('api-guide-txt'))}",
            f"# OpenAPI schema: {request.build_absolute_uri(reverse('openapi-schema'))}",
            "",
            f"Sitemap: {request.build_absolute_uri(reverse('sitemap-xml'))}",
            "",
        ]
        return HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")


@extend_schema(exclude=True)
class SitemapXmlView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request, *args, **kwargs):
        urls = [
            {
                "loc": request.build_absolute_uri(reverse("landing")),
                "lastmod": None,
            },
            {
                "loc": request.build_absolute_uri(reverse("public-company-directory")),
                "lastmod": None,
            },
        ]

        organizations = (
            verified_public_organization_queryset()
            .select_related("subscription")
            .order_by("slug")
        )
        for org in organizations:
            lastmod = org.updated_at.date().isoformat()
            languages = profile_language_choices(org)
            alternates = [
                {"language": item["code"], "url": public_profile_url(org, item["code"], request)}
                for item in languages
            ]
            alternates.append({
                "language": "x-default",
                "url": public_profile_url(org, primary_public_language(org), request),
            })
            for item in languages:
                urls.append({
                    "loc": public_profile_url(org, item["code"], request),
                    "lastmod": lastmod,
                    "alternates": alternates,
                })

        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">',
        ]
        for item in urls:
            lines.append("  <url>")
            lines.append(f"    <loc>{escape(item['loc'])}</loc>")
            if item["lastmod"]:
                lines.append(f"    <lastmod>{item['lastmod']}</lastmod>")
            for alternate in item.get("alternates", []):
                lines.append(
                    f'    <xhtml:link rel="alternate" hreflang="{escape(alternate["language"])}" href="{escape(alternate["url"])}" />'
                )
            lines.append("  </url>")
        lines.append("</urlset>")
        return HttpResponse("\n".join(lines), content_type="application/xml; charset=utf-8")
