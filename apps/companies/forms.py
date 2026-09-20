import json
import re
from django.db import transaction
from urllib.parse import urlsplit

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.utils.translation import get_language

from .models import (
    ContentEntry,
    EntryType,
    Organization,
    OrganizationType,
    Product,
    SocialNetwork,
    SocialProfile,
    Tag,
)


LANGUAGE_CHOICES = list(settings.LANGUAGES)
FEED_LANGUAGE_CHOICES = list(getattr(settings, "FEED_LANGUAGES", settings.LANGUAGES))

LANGUAGE_LABELS = {
    "en": {
        "pl": "Polish",
        "en": "English",
        "es": "Spanish",
        "it": "Italian",
        "de": "German",
        "fr": "French",
    },
    "pl": {
        "pl": "Polski",
        "en": "Angielski",
        "es": "Hiszpański",
        "it": "Włoski",
        "de": "Niemiecki",
        "fr": "Francuski",
    },
}

POLISH_FIELD_LABELS = {
    "name": "Nazwa firmy",
    "company_type": "Typ firmy",
    "website_url": "Adres strony WWW",
    "contact_email": "E-mail kontaktowy",
    "phone_number": "Numer telefonu",
    "address_line": "Adres",
    "city": "Miasto",
    "postal_code": "Kod pocztowy",
    "country": "Kraj",
}

ENGLISH_COMPANY_TYPE_CHOICES = [
    (OrganizationType.MANUFACTURING, "Manufacturing"),
    (OrganizationType.SERVICES, "Services"),
    (OrganizationType.TRADING, "Trading"),
    (OrganizationType.OTHER, "Other"),
]

POLISH_COMPANY_TYPE_CHOICES = [
    (OrganizationType.MANUFACTURING, "Produkcyjna"),
    (OrganizationType.SERVICES, "Usługowa"),
    (OrganizationType.TRADING, "Handlowa"),
    (OrganizationType.OTHER, "Inna"),
]

DESCRIPTION_HELP_TEXTS = {
    "en": {
        "short_description": (
            "Add 1-2 clear sentences about what your company does, who it helps, and your key value. "
            "Use simple keywords AI search engines can match quickly."
        ),
        "long_description": (
            "Write a fuller company profile: services/products, ideal customers, industries, locations, "
            "and what makes you different. Use natural, factual language so AI tools can understand and cite it."
        ),
    },
    "pl": {
        "short_description": (
            "Dodaj 1-2 krótkie zdania: czym zajmuje się firma, komu pomaga i jaka jest jej główna wartość. "
            "Używaj prostych słów kluczowych, które AI łatwo dopasuje."
        ),
        "long_description": (
            "Napisz pełniejszy opis firmy: usługi/produkty, idealni klienci, branże, lokalizacje i przewagi. "
            "Używaj naturalnego, konkretnego języka, aby wyszukiwarki AI mogły to poprawnie zrozumieć i cytować."
        ),
    },
}

LANGUAGE_BUTTON_HELP = {
    "en": "AI search engines read and understand content better in their native language. Add descriptions in the language of the country where you want to appear in AI search results.",
    "pl": "Wyszukiwarki AI czytają i rozumieją treści lepiej w swoim naturalnym języku. Dodaj opisy w języku kraju, w którym chcesz się pojawiać w wynikach wyszukiwania AI.",
}


class OrganizationForm(forms.ModelForm):
    # Języki faktycznie wspierane przez model i ustawienia aplikacji.
    AVAILABLE_LANGUAGES = [code for code, _label in FEED_LANGUAGE_CHOICES]
    website_url = forms.CharField(required=True, widget=forms.TextInput())
    social_profiles_text = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 4}))
    ai_summary = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, language_code: str | None = None, organization: Organization | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.plan_features = organization.get_subscription().feature_matrix() if organization else {"languages": 1, "tags": 0, "products": 0, "social_profiles": 0, "content_entries": 0}
        
        ui_language = "pl" if (language_code or get_language() or "en")[:2] == "pl" else "en"
        allowed_languages_count = self._get_allowed_languages_count(organization)
        
        # Jeśli edycja - pobierz zaznaczone języki z instancji, inaczej domyślnie PL
        if self.instance and self.instance.pk and self.instance.content_languages:
            selected_languages = [
                code for code in dict.fromkeys([self.instance.primary_language] + self.instance.content_languages)
                if code in self.AVAILABLE_LANGUAGES
            ]
        else:
            selected_languages = [ui_language]

        if self.is_bound:
            try:
                submitted = self._submitted_languages()
                if isinstance(submitted, list):
                    selected_languages = list(dict.fromkeys(code for code in submitted if code in self.AVAILABLE_LANGUAGES))
            except (ValueError, TypeError):
                pass

        if not selected_languages:
            selected_languages = ["pl"]

        if not self.is_bound:
            selected_languages = selected_languages[:allowed_languages_count]

        self.initial_descriptions = self._build_initial_descriptions(selected_languages)
        self.initial_language_tags = self._build_initial_language_tags(selected_languages)
        self.initial_language_products = self._build_initial_language_products(selected_languages)
        
        # Store metadata dla template
        self.ui_language = ui_language
        self.allowed_languages_count = allowed_languages_count
        self.selected_languages = selected_languages
        self.language_labels = LANGUAGE_LABELS[ui_language]
        self.description_helps = DESCRIPTION_HELP_TEXTS[ui_language]
        self.language_button_help = LANGUAGE_BUTTON_HELP[ui_language]
        self.available_languages = self.AVAILABLE_LANGUAGES
        
        # Usuń wszystkie pola opisów - będą wyświetlane dynamicznie w template
        for lang_code in self.AVAILABLE_LANGUAGES:
            self.fields.pop(f"short_description_{lang_code}", None)
            self.fields.pop(f"long_description_{lang_code}", None)
        
        # Usuń tylko content_languages z formularza (primary_language jest wybierany przez użytkownika)
        self.fields.pop("content_languages", None)

        if "primary_language" in self.fields:
            self.fields["primary_language"].required = False
            self.fields["primary_language"].choices = [
                (code, self.language_labels.get(code, code.upper()))
                for code in self.AVAILABLE_LANGUAGES
            ]
            primary_initial = (
                getattr(self.instance, "primary_language", "")
                if self.instance and self.instance.pk
                else (selected_languages[0] if selected_languages else "pl")
            )
            if primary_initial not in self.AVAILABLE_LANGUAGES:
                primary_initial = selected_languages[0] if selected_languages else "pl"
            self.fields["primary_language"].initial = primary_initial

        if "company_type" in self.fields:
            self.fields["company_type"].choices = (
                POLISH_COMPANY_TYPE_CHOICES if ui_language == "pl" else ENGLISH_COMPANY_TYPE_CHOICES
            )
            self.fields["company_type"].label = "Typ firmy" if ui_language == "pl" else "Company type"
            self.fields["company_type"].widget.attrs.update(
                {
                    "class": "w-full appearance-none rounded border border-[#00d4aa]/40 bg-[#0d1117] px-3 py-2 pr-10 font-mono text-xs text-slate-200 transition hover:border-[#00d4aa]/60 focus:border-[#00d4aa] focus:outline-none focus:ring-2 focus:ring-[#00d4aa]/20",
                }
            )

        if "name" in self.fields:
            self.fields["name"].label = "Nazwa firmy" if ui_language == "pl" else "Brand name"
            self.fields["name"].widget.attrs.update(
                {
                    "placeholder": "np. XOAILA" if ui_language == "pl" else "e.g. XOAILA",
                }
            )

        if "website_url" in self.fields:
            self.fields["website_url"].widget.attrs.update(
                {
                    "type": "text",
                    "inputmode": "url",
                    "autocomplete": "url",
                    "placeholder": "twojadomena.pl" if ui_language == "pl" else "yourdomain.com",
                }
            )

        if "contact_email" in self.fields:
            self.fields["contact_email"].widget.attrs.update(
                {
                    "placeholder": "kontakt@twojadomena.pl" if ui_language == "pl" else "contact@yourdomain.com",
                }
            )

        if "phone_number" in self.fields:
            self.fields["phone_number"].widget.attrs.update(
                {
                    "placeholder": "+48 123 456 789" if ui_language == "pl" else "+1 555 123 4567",
                }
            )

        if "address_line" in self.fields:
            self.fields["address_line"].widget.attrs.update(
                {
                    "placeholder": "ul. Przykładowa 12" if ui_language == "pl" else "221B Baker Street",
                }
            )

        if "city" in self.fields:
            self.fields["city"].widget.attrs.update(
                {
                    "placeholder": "Warszawa" if ui_language == "pl" else "London",
                }
            )

        if "postal_code" in self.fields:
            self.fields["postal_code"].widget.attrs.update(
                {
                    "placeholder": "00-001" if ui_language == "pl" else "SW1A 1AA",
                }
            )

        if "country" in self.fields:
            self.fields["country"].widget.attrs.update(
                {
                    "placeholder": "Polska" if ui_language == "pl" else "United Kingdom",
                }
            )

        if "ai_summary" in self.fields:
            self.fields["ai_summary"].widget.attrs.update(
                {
                    "placeholder": (
                        'np. "Dobra do MVP dla startupów, aplikacji SaaS i projektów Django"'
                        if ui_language == "pl"
                        else 'e.g. "Great for startup MVPs, SaaS apps, and Django projects"'
                    )
                }
            )

        if "social_profiles_text" in self.fields:
            self.fields["social_profiles_text"].widget.attrs.update(
                {
                    "placeholder": (
                        "linkedin.com/company/twoja-firma\nfacebook.com/twoja-firma"
                        if ui_language == "pl"
                        else "linkedin.com/company/your-company\nfacebook.com/your-company"
                    )
                }
            )

        self._setup_full_visibility_labels(ui_language)
        self._setup_default_widget_styles()
        self._hydrate_visibility_initial_data(organization)
        
        # Na koniec przetłumacz pozostałe pola na PL jeśli trzeba
        if ui_language == "pl":
            for field_name, label in POLISH_FIELD_LABELS.items():
                if field_name in self.fields:
                    self.fields[field_name].label = label

        self.fields["company_type"].widget.attrs["class"] = self.fields["name"].widget.attrs["class"]
        self.fields["contact_email"].help_text = (
            "Ten adres będzie publiczny. Podaj firmowy adres kontaktowy, nie prywatny adres logowania."
            if ui_language == "pl" else "This address will be public. Use a business contact address, not your private login email."
        )
        for name, label in {
            "name": "Nazwa firmy" if ui_language == "pl" else "Company name",
            "website_url": "Strona internetowa" if ui_language == "pl" else "Website",
            "primary_language": "Główny język profilu" if ui_language == "pl" else "Main profile language",
        }.items():
            self.fields[name].label = label
        self.fields["primary_language"].help_text = "Ten język wyświetlamy jako pierwszy." if ui_language == "pl" else "This language is shown first when someone reads your profile."
        guidance = {
            "name": ("np. Zielony Ogród", "e.g. Green Garden", "Nazwa, pod którą klienci znają Twoją firmę. Pojawi się jako tytuł profilu.", "The name customers know you by. This becomes your profile title."),
            "website_url": ("twojafirma.pl", "yourcompany.com", "Oficjalna strona firmy. Możesz wpisać samą domenę; dodamy https://.", "Your official company website. Enter a domain; we will add https:// for you."),
            "country": ("np. Polska", "e.g. Poland", "Kraj siedziby firmy.", "The country where your company is based."),
            "city": ("np. Kraków", "e.g. London", "Miasto siedziby lub głównej lokalizacji.", "Your main business location."),
            "address_line": ("np. ul. Ogrodowa 12", "e.g. 12 Garden Street", "Podaj tylko adres, który chcesz udostępnić publicznie.", "Only include an address you want to make public."),
            "postal_code": ("np. 30-001", "e.g. SW1A 1AA", "Kod pocztowy lokalizacji firmy.", "The postal code for your business address."),
            "phone_number": ("np. +48 123 456 789", "e.g. +44 20 7946 0000", "Publiczny numer kontaktowy, najlepiej z numerem kierunkowym kraju.", "A public contact number, preferably including the country code."),
            "contact_email": ("kontakt@twojafirma.pl", "hello@yourcompany.com", "Widoczny publicznie. Podaj adres firmowy do zapytań od klientów.", "Visible publicly. Use a business address for customer enquiries."),
        }
        for field_name, (pl_example, en_example, pl_help, en_help) in guidance.items():
            self.fields[field_name].widget.attrs["placeholder"] = pl_example if ui_language == "pl" else en_example
            self.fields[field_name].help_text = pl_help if ui_language == "pl" else en_help
        for field_name, autocomplete in {"name": "organization", "contact_email": "email", "phone_number": "tel", "country": "country-name", "city": "address-level2", "address_line": "street-address", "postal_code": "postal-code"}.items():
            self.fields[field_name].widget.attrs["autocomplete"] = autocomplete
        self.fields["phone_number"].widget.attrs["inputmode"] = "tel"
        self.fields["company_type"].help_text = "Wybierz kategorię najbliższą Twojej głównej działalności." if ui_language == "pl" else "Choose the category closest to your main business activity."
        self.basic_fields = [self[name] for name in ("name", "website_url", "company_type")]
        self.contact_fields = [self[name] for name in ("contact_email", "phone_number", "country", "city", "address_line", "postal_code")]
        descriptions = self._build_initial_descriptions(self.AVAILABLE_LANGUAGES)
        tags = self._build_initial_language_tags(self.AVAILABLE_LANGUAGES)
        products = self._build_initial_language_products(self.AVAILABLE_LANGUAGES)
        self.language_sections = [
            {"code": code, "label": self.language_labels.get(code, code.upper()), "selected": code in selected_languages,
             "short": descriptions[code]["short"], "long": descriptions[code]["long"], "tags": tags[code], "products": products[code]}
            for code in self.AVAILABLE_LANGUAGES
        ]
        description_examples = {
            "pl": "Projektujemy i pielęgnujemy ogrody dla właścicieli domów w Krakowie i okolicach.",
            "en": "We design and maintain gardens for homeowners in London and the surrounding area.",
            "de": "Wir gestalten und pflegen Gärten für Hausbesitzer in Berlin und Umgebung.",
            "es": "Diseñamos y cuidamos jardines para propietarios de viviendas en Madrid y sus alrededores.",
            "it": "Progettiamo e curiamo giardini per i proprietari di case a Roma e dintorni.",
            "fr": "Nous concevons et entretenons des jardins pour les propriétaires à Lyon et dans les environs.",
        }
        for section in self.language_sections:
            section["example"] = description_examples.get(section["code"], description_examples["en"])
        existing_products = list(self.instance.products.all()) if self.instance.pk and self.plan_features["products"] else []
        existing_faqs = list(
            self.instance.content_entries.filter(entry_type=EntryType.FAQ).order_by("-is_featured", "pk")
        ) if self.instance.pk and self.plan_features["content_entries"] else []
        for section in self.language_sections:
            code = section["code"]
            if self.is_bound and f"product_rows_{code}" in self.data:
                try:
                    rows = json.loads(self.data[f"product_rows_{code}"])
                    section["product_rows"] = rows if isinstance(rows, list) else []
                except (ValueError, TypeError):
                    section["product_rows"] = []
            elif not self.is_bound:
                section["product_rows"] = [product.translation_for_editor(code) for product in existing_products]
            if self.is_bound and f"faq_rows_{code}" in self.data:
                try:
                    rows = json.loads(self.data[f"faq_rows_{code}"])
                    section["faq_rows"] = rows if isinstance(rows, list) else []
                except (ValueError, TypeError):
                    section["faq_rows"] = []
            elif not self.is_bound:
                section["faq_rows"] = [entry.translation_for_editor(code) for entry in existing_faqs]

    def _submitted_languages(self):
        if "languages" in self.data:
            return self.data.getlist("languages") if hasattr(self.data, "getlist") else self.data["languages"]
        return json.loads(self.data.get("content_languages", "[]"))

    def _setup_full_visibility_labels(self, ui_language: str) -> None:
        if ui_language == "pl":
            labels = {
                "primary_language": "Domyślny język feedu",
                "social_profiles_text": "Profile społecznościowe (linki)",
            }
            helps = {
                "primary_language": "To główny język profilu. Jest używany domyślnie w publicznych formatach danych.",
                "social_profiles_text": "Wklej tylko te linki, które firma faktycznie posiada (po jednym w linii). Obsługiwane: Facebook, Instagram, LinkedIn, X, TikTok, YouTube.",
            }
            labels["ai_summary"] = "Dla jakich klient\u00f3w/projekt\u00f3w ta firma jest najlepsza?"
            helps["ai_summary"] = "Kr\u00f3tko opisz, dla jakich klient\u00f3w, bran\u017c albo projekt\u00f3w ta firma pasuje najlepiej."
        else:
            labels = {
                "primary_language": "Default feed language",
                "social_profiles_text": "Social profiles (links)",
                "ai_summary": "What clients/projects is this company best for?",
            }
            helps = {
                "primary_language": "This is the main profile language used by default in public data formats.",
                "social_profiles_text": "Paste only existing profile links (one per line). Supported: Facebook, Instagram, LinkedIn, X, TikTok, YouTube.",
                "ai_summary": "Briefly describe what kinds of clients, industries, or projects this company fits best.",
            }

        for field_name, label in labels.items():
            if field_name in self.fields:
                self.fields[field_name].label = label
        for field_name, help_text in helps.items():
            if field_name in self.fields:
                self.fields[field_name].help_text = help_text

    def _setup_default_widget_styles(self) -> None:
        input_css = (
            "w-full rounded-lg border border-slate-200 bg-white px-3 py-2.5 "
            "text-sm text-slate-800 placeholder-slate-400 transition "
            "hover:border-slate-300 focus:border-slate-400 focus:outline-none "
            "focus:ring-2 focus:ring-slate-800/10"
        )
        textarea_css = (
            "w-full rounded-lg border border-slate-200 bg-white px-3 py-2.5 "
            "text-sm text-slate-800 placeholder-slate-400 transition "
            "hover:border-slate-300 focus:border-slate-400 focus:outline-none "
            "focus:ring-2 focus:ring-slate-800/10"
        )

        for field_name, field in self.fields.items():
            if field_name == "company_type":
                continue
            if isinstance(field.widget, forms.CheckboxInput):
                continue
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs["class"] = textarea_css
                continue
            field.widget.attrs["class"] = input_css

    def _hydrate_visibility_initial_data(self, organization: Organization | None) -> None:
        org = self.instance if self.instance and self.instance.pk else organization
        if not org or not getattr(org, "pk", None):
            return

        if "social_profiles_text" in self.fields:
            social_urls = list(org.social_profiles.order_by("network").values_list("url", flat=True))
            self.fields["social_profiles_text"].initial = "\n".join(social_urls)

    def clean_website_url(self):
        website_url = (self.cleaned_data.get("website_url") or "").strip()
        if not website_url:
            raise forms.ValidationError(
                "Adres strony WWW jest wymagany." if self.ui_language == "pl"
                else "Website URL is required."
            )

        parsed_url = urlsplit(website_url)
        normalized_url = website_url if parsed_url.scheme else f"https://{website_url}"

        try:
            URLValidator(schemes=["http", "https"])(normalized_url)
        except ValidationError:
            raise forms.ValidationError(
                "Podaj poprawny adres strony WWW." if self.ui_language == "pl"
                else "Enter a valid website URL."
            )

        return normalized_url

    def _normalize_optional_url(self, raw_value, *, invalid_message_pl: str, invalid_message_en: str) -> str:
        value = (raw_value or "").strip()
        if not value:
            return ""

        parsed = urlsplit(value)
        normalized = value if parsed.scheme else f"https://{value}"
        try:
            URLValidator(schemes=["http", "https"])(normalized)
        except ValidationError:
            raise forms.ValidationError(invalid_message_pl if self.ui_language == "pl" else invalid_message_en)
        return normalized

    def clean(self):
        cleaned_data = super().clean()
        
        # Pobierz zaznaczone języki z POST data
        content_languages_str = self.data.get("content_languages", "[]")
        try:
            content_languages = self._submitted_languages()
        except json.JSONDecodeError:
            raise forms.ValidationError("Invalid content languages format.")

        if not isinstance(content_languages, list):
            raise forms.ValidationError("Invalid content languages format.")

        content_languages = [str(code) for code in content_languages]
        content_languages = [code for code in content_languages if code in self.AVAILABLE_LANGUAGES]

        if len(content_languages) != len(set(content_languages)):
            raise forms.ValidationError(
                "Każdy język może być wybrany tylko raz." if self.ui_language == "pl"
                else "Each language can be selected only once."
            )
        
        if not content_languages:
            raise forms.ValidationError(
                "Musisz wybrać co najmniej jeden język." if self.ui_language == "pl" else "You must select at least one language."
            )

        if len(content_languages) > self.allowed_languages_count:
            raise forms.ValidationError(
                f"Twój plan pozwala na maksymalnie {self.allowed_languages_count} języki."
                if self.ui_language == "pl"
                else f"Your plan allows up to {self.allowed_languages_count} languages."
            )

        primary_language = (cleaned_data.get("primary_language") or "").strip()
        if not primary_language:
            primary_language = content_languages[0]
            cleaned_data["primary_language"] = primary_language
        if primary_language not in self.AVAILABLE_LANGUAGES:
            raise forms.ValidationError(
                "Wybierz poprawny domyślny język feedu."
                if self.ui_language == "pl"
                else "Select a valid default feed language."
            )
        if primary_language not in content_languages:
            raise forms.ValidationError(
                "Domyślny język feedu musi znajdować się na liście wybranych języków."
                if self.ui_language == "pl"
                else "Default feed language must be included in selected feed languages."
            )
        
        products = self._parse_products_by_language(content_languages)
        faqs = self._parse_faqs_by_language(content_languages)
        primary_faqs = faqs.get(primary_language, [])
        existing_non_faqs = (
            self.instance.content_entries.exclude(entry_type=EntryType.FAQ).count()
            if self.instance and self.instance.pk else 0
        )
        counts = {
            "products": max((len(items) for items in products.values()), default=0),
            "tags": sum(len(self._parse_tag_chunks(self.data.get(f"tags_{code}", ""))) for code in content_languages),
            "social_profiles": len(self._parse_social_profiles_text(cleaned_data.get("social_profiles_text", ""))),
            "content_entries": existing_non_faqs + len(primary_faqs),
        }
        for resource, count in counts.items():
            limit = self.plan_features.get(resource, 0)
            existing_count = getattr(self.instance, resource).count() if self.instance and self.instance.pk and hasattr(self.instance, resource) else 0
            if count > max(limit, existing_count):
                raise forms.ValidationError(f"Your plan allows up to {self.plan_features.get(resource, 0)} {resource.replace('_', ' ')}.")
        for code in content_languages:
            if len(self.data.get(f"short_description_{code}", "")) > 280 or len(self.data.get(f"long_description_{code}", "")) > 20000:
                label = self.language_labels.get(code, code)
                message = f"{label}: skróć opis do 280 znaków, a opis szczegółowy do 20 000 znaków." if self.ui_language == "pl" else f"{label}: keep the short description within 280 characters and the detailed description within 20,000."
                for section in self.language_sections:
                    if section["code"] == code:
                        section["error"] = message
                self.add_error(None, message)

        social_raw = (cleaned_data.get("social_profiles_text") or "").strip()
        if social_raw:
            self._parse_social_profiles_text(social_raw)

        return cleaned_data

    @transaction.atomic
    def save(self, commit=True):
        instance = super().save(commit=False)
        archived_descriptions = {}
        if commit and instance.owner_id:
            from apps.accounts.models import User
            owner = User.objects.select_for_update().get(pk=instance.owner_id)
            instance.owner = owner
            if not instance.pk and not owner.can_add_organization():
                raise forms.ValidationError("Your company limit has been reached.")
            if instance.pk:
                previous = Organization.objects.select_for_update().get(pk=instance.pk)
                if len(previous.content_languages or []) > instance.get_subscription().limit_for("languages"):
                    archived_descriptions = dict(previous.descriptions_by_language or {})
            instance.owner = owner
            self.plan_features = instance.get_subscription().feature_matrix()
            self.allowed_languages_count = self.plan_features.get("languages", 1)
            self.clean()
        
        # Pobierz zaznaczone języki z POST data
        content_languages_str = self.data.get("content_languages", "[]")
        try:
            selected_languages = self._submitted_languages()
            if not isinstance(selected_languages, list):
                selected_languages = ["pl"]
        except json.JSONDecodeError:
            selected_languages = ["pl"]

        selected_languages = [str(code) for code in selected_languages]
        selected_languages = [code for code in selected_languages if code in self.AVAILABLE_LANGUAGES]

        if not selected_languages:
            selected_languages = ["pl"]

        # kolejność i unikalność
        selected_languages = list(dict.fromkeys(selected_languages))
        instance.content_languages = selected_languages[:self.allowed_languages_count]
        primary_language = (self.cleaned_data.get("primary_language") or "").strip()
        if primary_language in instance.content_languages:
            instance.primary_language = primary_language
        elif instance.content_languages:
            instance.primary_language = instance.content_languages[0]

        description_payload: dict[str, dict[str, str]] = {}
        
        # Zapisz opisy wybrane przez użytkownika z POST data
        for lang_code in instance.content_languages:
            short_field = f"short_description_{lang_code}"
            long_field = f"long_description_{lang_code}"
            short_value = self.data.get(short_field, "").strip()
            long_value = self.data.get(long_field, "").strip()

            description_payload[lang_code] = {
                "short": short_value,
                "long": long_value,
            }

            if hasattr(instance, short_field):
                setattr(instance, short_field, short_value)
            if hasattr(instance, long_field):
                setattr(instance, long_field, long_value)

        instance.descriptions_by_language = {**archived_descriptions, **description_payload}

        # Keep legacy EN/PL columns synchronized for compatibility with old reads.
        if "en" not in instance.content_languages:
            instance.short_description_en = ""
            instance.long_description_en = ""
        if "pl" not in instance.content_languages:
            instance.short_description_pl = ""
            instance.long_description_pl = ""
        
        if commit:
            instance.save()
            if self.plan_features["tags"]:
                self._save_tags(instance, instance.content_languages)
            if self.plan_features["social_profiles"]:
                self._save_social_profiles(instance)
            if self.plan_features["products"]:
                self._save_products(instance, instance.content_languages)
            if self.plan_features["content_entries"]:
                self._save_faq_entries(instance, instance.content_languages)
        return instance

    def _parse_tag_chunks(self, raw_value: str) -> list[str]:
        chunks = [chunk.strip() for chunk in re.split(r"[,;\n]+", raw_value) if chunk.strip()]
        deduped: list[str] = []
        seen: set[str] = set()
        for chunk in chunks:
            key = chunk.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(chunk[:80])
        return deduped

    def _save_tags(self, instance: Organization, selected_languages: list[str]) -> None:
        language_tags: dict[str, list[str]] = {}
        for language_code in selected_languages:
            raw_language_tags = (self.data.get(f"tags_{language_code}") or "").strip()
            if raw_language_tags:
                language_tags[language_code] = self._parse_tag_chunks(raw_language_tags)

        instance.tags.all().delete()

        for language_code, names in language_tags.items():
            for tag_name in names:
                Tag.objects.create(
                    organization=instance,
                    name=tag_name,
                    language=language_code,
                )

    def _save_social_profiles(self, instance: Organization) -> None:
        raw_social = (self.cleaned_data.get("social_profiles_text") or "").strip()
        parsed = self._parse_social_profiles_text(raw_social) if raw_social else {}

        instance.social_profiles.all().delete()
        for network, url in parsed.items():
            SocialProfile.objects.create(
                organization=instance,
                network=network,
                url=url,
            )

    def _parse_products_text(self, raw_value: str) -> list[dict[str, str]]:
        # Support both comma-separated (new) and pipe-per-line (legacy) formats
        # If input contains '|' treat as legacy pipe format, otherwise comma-separated
        if '|' in raw_value:
            lines = [line.strip() for line in raw_value.splitlines() if line.strip()]
        else:
            # Comma-separated: split by comma, each item is just a name
            lines = [item.strip() for item in raw_value.replace('\n', ',').split(',') if item.strip()]
            return [{"name": name[:255], "description": "", "url": ""} for name in lines]

        parsed: list[dict[str, str]] = []
        for line in lines:
            parts = [part.strip() for part in line.split("|")]
            if not parts or not parts[0]:
                continue
            name = parts[0][:255]
            description = parts[1] if len(parts) > 1 else ""
            url_raw = parts[2] if len(parts) > 2 else ""
            normalized_url = ""
            if url_raw:
                normalized_url = self._normalize_optional_url(
                    url_raw,
                    invalid_message_pl=f"Niepoprawny link produktu: {url_raw}",
                    invalid_message_en=f"Invalid product URL: {url_raw}",
                )
            parsed.append({"name": name, "description": description, "url": normalized_url})
        return parsed

    def _parse_products_by_language(self, selected_languages: list[str]) -> dict[str, list[dict[str, str]]]:
        payload: dict[str, list[dict[str, str]]] = {}
        for language_code in selected_languages:
            structured = self.data.get(f"product_rows_{language_code}")
            if structured is not None:
                try:
                    rows = json.loads(structured)
                    if not isinstance(rows, list) or len(rows) > self.plan_features.get("products", 0):
                        raise ValueError()
                    parsed = []
                    for row in rows:
                        if not isinstance(row, dict) or any(not isinstance(row.get(key, ""), str) for key in ("name", "description", "url")):
                            raise ValueError()
                        name, description, url = (row.get(key, "").strip() for key in ("name", "description", "url"))
                        if (not name and language_code == self.data.get("primary_language")) or len(name) > 255 or len(description) > 280:
                            raise ValueError()
                        if url:
                            url = self._normalize_optional_url(url, invalid_message_pl="Niepoprawny adres produktu.", invalid_message_en="Enter a valid product website.")
                        parsed.append({"name": name, "description": description, "url": url})
                    payload[language_code] = parsed
                    continue
                except (ValueError, TypeError):
                    raise forms.ValidationError("Check product names, descriptions (up to 280 characters), and your plan's product limit.")
            raw_products = (self.data.get(f"products_{language_code}") or "").strip()
            payload[language_code] = self._parse_products_text(raw_products) if raw_products else []
        return payload

    def _save_products(self, instance, selected_languages):
        payload = self._parse_products_by_language(selected_languages)
        existing = list(instance.products.order_by("pk"))
        retained = set()
        primary = payload.get(instance.primary_language, [])
        for index, item in enumerate(primary):
            available = [product for product in existing if product.pk not in retained]
            product = next((product for product in available if (item["url"] and product.product_url == item["url"]) or product.name == item["name"]), None)
            if product is None:
                # The text editor has no IDs; preserve the existing row for a rename.
                product = next((product for product in available if product.name not in {row["name"] for row in primary[index+1:]}), None)
            product = product or Product(organization=instance)
            product.name = item["name"]
            product.product_url = item["url"]
            product.is_featured = index == 0
            names = dict(product.names_by_language or {})
            translations = dict(product.descriptions_by_language or {})
            for code, rows in payload.items():
                translated = next((row for row in rows if item["url"] and row["url"] == item["url"]), rows[index] if index < len(rows) else None)
                if translated is not None:
                    names[code] = translated["name"]
                    translations[code] = translated["description"]
                    if code in {"en", "pl"}:
                        setattr(product, f"short_description_{code}", translated["description"][:280])
            product.names_by_language = names
            product.descriptions_by_language = translations
            product.save()
            retained.add(product.pk)
        instance.products.exclude(pk__in=retained).delete()
        # Product rows are authoritative; retain the old JSON column only for migration.

    def _parse_faqs_by_language(self, selected_languages: list[str]) -> dict[str, list[dict]]:
        payload: dict[str, list[dict]] = {}
        plan_limit = self.plan_features.get("content_entries", 0)
        existing_count = self.instance.content_entries.count() if self.instance and self.instance.pk else 0
        maximum = max(plan_limit, existing_count)
        for language_code in selected_languages:
            raw = self.data.get(f"faq_rows_{language_code}", "[]")
            try:
                rows = json.loads(raw)
                if not isinstance(rows, list) or len(rows) > maximum:
                    raise ValueError()
                parsed = []
                for row in rows:
                    if not isinstance(row, dict):
                        raise ValueError()
                    question = str(row.get("question", "")).strip()
                    answer = str(row.get("answer", "")).strip()
                    url = str(row.get("url", "")).strip()
                    entry_id = row.get("id")
                    if not any((question, answer, url)):
                        continue
                    if len(question) > 255 or len(answer) > 2000 or len(url) > 200:
                        raise ValueError()
                    if bool(question) != bool(answer):
                        raise forms.ValidationError(
                            "Każdy wpis FAQ musi zawierać pytanie i odpowiedź."
                            if self.ui_language == "pl" else "Each FAQ entry must include both a question and an answer."
                        )
                    if language_code == self.data.get("primary_language") and not question:
                        raise ValueError()
                    if url:
                        url = self._normalize_optional_url(
                            url,
                            invalid_message_pl="Niepoprawny adres źródła FAQ.",
                            invalid_message_en="Enter a valid FAQ source URL.",
                        )
                    parsed.append({
                        "id": int(entry_id) if str(entry_id).isdigit() else None,
                        "question": question,
                        "answer": answer,
                        "url": url,
                    })
                payload[language_code] = parsed
            except (ValueError, TypeError, json.JSONDecodeError):
                raise forms.ValidationError(
                    "Sprawdź pytania FAQ, odpowiedzi (do 2000 znaków), adresy źródeł i limit planu."
                    if self.ui_language == "pl"
                    else "Check FAQ questions, answers (up to 2,000 characters), source URLs, and your plan limit."
                )
        return payload

    def _save_faq_entries(self, instance: Organization, selected_languages: list[str]) -> None:
        payload = self._parse_faqs_by_language(selected_languages)
        primary_rows = payload.get(instance.primary_language, [])
        existing = list(instance.content_entries.filter(entry_type=EntryType.FAQ).order_by("-is_featured", "pk"))
        existing_by_id = {entry.pk: entry for entry in existing}
        retained = set()
        for index, primary_row in enumerate(primary_rows):
            entry = existing_by_id.get(primary_row["id"])
            if entry is None and index < len(existing) and existing[index].pk not in retained:
                entry = existing[index]
            entry = entry or ContentEntry(organization=instance, entry_type=EntryType.FAQ)
            questions = dict(entry.questions_by_language or {})
            answers = dict(entry.answers_by_language or {})
            source_url = primary_row["url"]
            for code in selected_languages:
                translated_rows = payload.get(code, [])
                translated = next(
                    (row for row in translated_rows if row["id"] and row["id"] == primary_row["id"]),
                    translated_rows[index] if index < len(translated_rows) else None,
                )
                if translated and translated["question"] and translated["answer"]:
                    questions[code] = translated["question"]
                    answers[code] = translated["answer"]
                    source_url = source_url or translated["url"]
                else:
                    questions.pop(code, None)
                    answers.pop(code, None)
            entry.entry_type = EntryType.FAQ
            entry.title = primary_row["question"]
            entry.questions_by_language = questions
            entry.answers_by_language = answers
            entry.content_url = source_url
            entry.is_featured = index == 0
            entry.summary_en = answers.get("en", "")[:280]
            entry.summary_pl = answers.get("pl", "")[:280]
            entry.save()
            retained.add(entry.pk)
        instance.content_entries.filter(entry_type=EntryType.FAQ).exclude(pk__in=retained).delete()

    def _parse_social_profiles_text(self, raw_value: str) -> dict[str, str]:
        candidates = [chunk.strip() for chunk in re.split(r"[\n,;]+", raw_value) if chunk.strip()]
        network_map = {
            "facebook.com": SocialNetwork.FACEBOOK,
            "instagram.com": SocialNetwork.INSTAGRAM,
            "linkedin.com": SocialNetwork.LINKEDIN,
            "x.com": SocialNetwork.X,
            "twitter.com": SocialNetwork.X,
            "tiktok.com": SocialNetwork.TIKTOK,
            "youtube.com": SocialNetwork.YOUTUBE,
            "youtu.be": SocialNetwork.YOUTUBE,
        }
        parsed: dict[str, str] = {}
        unsupported: list[str] = []

        for candidate in candidates:
            normalized = candidate if urlsplit(candidate).scheme else f"https://{candidate}"
            try:
                URLValidator(schemes=["http", "https"])(normalized)
            except ValidationError:
                raise forms.ValidationError(
                    f"Niepoprawny link social: {candidate}" if self.ui_language == "pl"
                    else f"Invalid social profile URL: {candidate}"
                )

            hostname = (urlsplit(normalized).hostname or "").lower()
            network = None
            for domain, network_code in network_map.items():
                if hostname == domain or hostname.endswith("." + domain):
                    network = network_code
                    break

            if not network:
                unsupported.append(candidate)
                continue

            parsed[network] = normalized

        if unsupported:
            message = ", ".join(unsupported)
            raise forms.ValidationError(
                f"Nieobsługiwane profile social: {message}. Obsługiwane: Facebook, Instagram, LinkedIn, X, TikTok, YouTube."
                if self.ui_language == "pl"
                else f"Unsupported social profile URLs: {message}. Supported: Facebook, Instagram, LinkedIn, X, TikTok, YouTube."
            )

        return parsed

    @staticmethod
    def _get_allowed_languages_count(organization: Organization | None) -> int:
        """Zwraca ile języków treści ma dostęp na bazie planu subskrypcji."""
        if not organization:
            return 1
        try:
            get_subscription = getattr(organization, "get_subscription", None)
            if not callable(get_subscription):
                return 1
            subscription = get_subscription()
            return subscription.feature_matrix().get("languages", 1)
        except Exception:
            return 1

    def get_json_data(self):
        """Zwraca JSON version metadata dla JavaScript."""
        return json.dumps({
            "description_helps": self.description_helps,
            "languageButtonHelp": self.language_button_help,
            "availableLanguages": self.AVAILABLE_LANGUAGES,
            "initialDescriptions": self.initial_descriptions,
            "initialLanguageTags": self.initial_language_tags,
            "initialLanguageProducts": self.initial_language_products,
        })

    def _build_initial_language_tags(self, selected_languages: list[str]) -> dict[str, str]:
        values: dict[str, str] = {lang: "" for lang in selected_languages}
        if not self.instance or not getattr(self.instance, "pk", None):
            if self.is_bound:
                for lang_code in selected_languages:
                    values[lang_code] = (self.data.get(f"tags_{lang_code}") or "").strip()
            return values

        tags_by_language: dict[str, list[str]] = {}
        for tag in self.instance.tags.exclude(language="").order_by("name"):
            tags_by_language.setdefault(tag.language, []).append(tag.name)

        for lang_code in selected_languages:
            if self.is_bound:
                values[lang_code] = (self.data.get(f"tags_{lang_code}") or "").strip()
            else:
                values[lang_code] = ", ".join(tags_by_language.get(lang_code, []))

        return values

    def _build_initial_language_products(self, selected_languages: list[str]) -> dict[str, str]:
        values: dict[str, str] = {lang: "" for lang in selected_languages}
        if self.is_bound:
            for lang_code in selected_languages:
                # Keep the textarea fallback in sync with the enhanced editor after errors.
                if self.data.get(f"product_rows_{lang_code}") is not None:
                    try:
                        rows = json.loads(self.data[f"product_rows_{lang_code}"])
                        values[lang_code] = "\n".join(f"{row.get('name', '')} | {row.get('description', '')} | {row.get('url', '')}" for row in rows if isinstance(row, dict))
                    except (ValueError, TypeError):
                        values[lang_code] = self.data.get(f"products_{lang_code}", "")
                else:
                    values[lang_code] = (self.data.get(f"products_{lang_code}") or "").strip()
            return values

        if self.instance and self.instance.pk:
            for code in selected_languages:
                values[code] = "\n".join(f"{row['name']} | {row['description']} | {row['url']}" for row in (product.translation_for_editor(code) for product in self.instance.products.all()))
        return values

    def _build_initial_descriptions(self, selected_languages: list[str]) -> dict:
        descriptions: dict[str, dict[str, str]] = {}
        stored_descriptions = getattr(self.instance, "descriptions_by_language", {}) or {}
        for lang_code in selected_languages:
            short_field = f"short_description_{lang_code}"
            long_field = f"long_description_{lang_code}"

            if self.is_bound:
                short_value = self.data.get(short_field, "")
                long_value = self.data.get(long_field, "")
            else:
                short_value = (
                    stored_descriptions.get(lang_code, {}).get("short")
                    if self.instance else ""
                ) or (
                    getattr(self.instance, short_field, "") if self.instance else ""
                )
                long_value = (
                    stored_descriptions.get(lang_code, {}).get("long")
                    if self.instance else ""
                ) or (
                    getattr(self.instance, long_field, "") if self.instance else ""
                )

            descriptions[lang_code] = {
                "short": short_value or "",
                "long": long_value or "",
            }

        return descriptions

    class Meta:
        model = Organization
        fields = [
            "name",
            "company_type",
            "website_url",
            "contact_email",
            "phone_number",
            "address_line",
            "city",
            "postal_code",
            "country",
            "primary_language",
            "social_profiles_text",
            "ai_summary",
        ]
