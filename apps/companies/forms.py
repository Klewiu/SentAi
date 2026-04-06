import json
import re
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
    "name": "Nazwa brandu",
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
    website_url = forms.CharField(required=False, widget=forms.TextInput())
    social_profiles_text = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 4}))
    featured_entry_type = forms.ChoiceField(required=False, choices=[("", "---------")] + list(EntryType.choices))
    featured_entry_summary = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    featured_entry_url = forms.CharField(required=False, widget=forms.TextInput())

    def __init__(self, *args, language_code: str | None = None, organization: Organization | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        
        ui_language = "pl" if (language_code or get_language() or "en")[:2] == "pl" else "en"
        allowed_languages_count = self._get_allowed_languages_count(organization)
        
        # Jeśli edycja - pobierz zaznaczone języki z instancji, inaczej domyślnie PL
        if self.instance and self.instance.pk and self.instance.content_languages:
            selected_languages = [
                code for code in self.instance.content_languages
                if code in self.AVAILABLE_LANGUAGES
            ]
        else:
            selected_languages = ["pl"]  # Zawsze zaczynamy z PL

        if not selected_languages:
            selected_languages = ["pl"]

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
            self.fields["name"].label = "Nazwa brandu" if ui_language == "pl" else "Brand name"
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

        if "featured_entry_url" in self.fields:
            self.fields["featured_entry_url"].widget.attrs.update(
                {
                    "type": "text",
                    "inputmode": "url",
                    "autocomplete": "url",
                    "placeholder": "twojadomena.pl/faq" if ui_language == "pl" else "yourdomain.com/faq",
                }
            )

        if "featured_entry_summary" in self.fields:
            self.fields["featured_entry_summary"].widget.attrs.update(
                {
                    "placeholder": (
                        "np. Jak nasze rozwiązanie pomaga skrócić czas obsługi klienta o 30%."
                        if ui_language == "pl"
                        else "e.g. How our solution helps reduce customer handling time by 30%."
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

        if "featured_entry_type" in self.fields:
            if ui_language == "pl":
                self.fields["featured_entry_type"].choices = [
                    ("", "Brak / pomiń"),
                    (EntryType.UPDATE, "Aktualność"),
                    (EntryType.FAQ, "FAQ"),
                    (EntryType.GUIDE, "Poradnik"),
                    (EntryType.CASE_STUDY, "Case study"),
                ]
            else:
                self.fields["featured_entry_type"].choices = [
                    ("", "None / skip"),
                    (EntryType.UPDATE, "Update"),
                    (EntryType.FAQ, "FAQ"),
                    (EntryType.GUIDE, "Guide"),
                    (EntryType.CASE_STUDY, "Case study"),
                ]

        self._setup_full_visibility_labels(ui_language)
        self._setup_default_widget_styles()
        self._hydrate_visibility_initial_data(organization)
        
        # Na koniec przetłumacz pozostałe pola na PL jeśli trzeba
        if ui_language == "pl":
            for field_name, label in POLISH_FIELD_LABELS.items():
                if field_name in self.fields:
                    self.fields[field_name].label = label

    def _setup_full_visibility_labels(self, ui_language: str) -> None:
        if ui_language == "pl":
            labels = {
                "primary_language": "Domyślny język feedu",
                "social_profiles_text": "Profile społecznościowe (linki)",
                "featured_entry_type": "Materiał wiedzy o firmie (opcjonalnie) - typ",
                "featured_entry_summary": "Materiał wiedzy o firmie (opcjonalnie) - krótki opis",
                "featured_entry_url": "Materiał wiedzy o firmie (opcjonalnie) - link",
            }
            helps = {
                "primary_language": "To główny język feedu. Jest używany jako domyślny język opisów i fallback w kanałach AI.",
                "social_profiles_text": "Wklej tylko te linki, które firma faktycznie posiada (po jednym w linii). Obsługiwane: Facebook, Instagram, LinkedIn, X, TikTok, YouTube.",
                "featured_entry_type": "To opcjonalne. Wybierz, jeśli masz treść edukacyjną o firmie (FAQ/poradnik/case study/aktualność).",
                "featured_entry_summary": "1-3 zdania: co klient znajdzie w materiale i dla kogo jest ta treść.",
                "featured_entry_url": "Pełny adres URL do konkretnego artykułu, FAQ lub case study na Twojej stronie.",
            }
        else:
            labels = {
                "primary_language": "Default feed language",
                "social_profiles_text": "Social profiles (links)",
                "featured_entry_type": "Knowledge content about the company (optional) - type",
                "featured_entry_summary": "Knowledge content about the company (optional) - short summary",
                "featured_entry_url": "Knowledge content about the company (optional) - URL",
            }
            helps = {
                "primary_language": "This is the default feed language used as primary description language and fallback in AI channels.",
                "social_profiles_text": "Paste only existing profile links (one per line). Supported: Facebook, Instagram, LinkedIn, X, TikTok, YouTube.",
                "featured_entry_type": "Optional. Use this if you have educational content such as FAQ, guide, case study, or update.",
                "featured_entry_summary": "1-3 sentences about what users will learn and who the content is for.",
                "featured_entry_url": "Direct URL to the article, FAQ, guide, or case study on your website.",
            }

        for field_name, label in labels.items():
            if field_name in self.fields:
                self.fields[field_name].label = label
        for field_name, help_text in helps.items():
            if field_name in self.fields:
                self.fields[field_name].help_text = help_text

    def _setup_default_widget_styles(self) -> None:
        input_css = (
            "w-full rounded border border-white/10 bg-white/5 px-3 py-2 "
            "font-mono text-sm text-white placeholder-slate-500 transition "
            "hover:border-white/20 focus:border-[#00d4aa] focus:outline-none "
            "focus:ring-2 focus:ring-[#00d4aa]/20"
        )
        textarea_css = (
            "w-full rounded border border-white/10 bg-white/5 px-3 py-2 "
            "font-mono text-sm text-white placeholder-slate-500 transition "
            "hover:border-white/20 focus:border-[#00d4aa] focus:outline-none "
            "focus:ring-2 focus:ring-[#00d4aa]/20"
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

        featured_entry = org.content_entries.filter(is_featured=True).order_by("-published_at", "title").first()
        if featured_entry:
            self.fields["featured_entry_type"].initial = featured_entry.entry_type
            self.fields["featured_entry_summary"].initial = featured_entry.localized_summary(org.primary_language)
            self.fields["featured_entry_url"].initial = featured_entry.content_url

    def clean_website_url(self):
        website_url = (self.cleaned_data.get("website_url") or "").strip()
        if not website_url:
            return ""

        parsed_url = urlsplit(website_url)
        normalized_url = website_url if parsed_url.scheme else f"https://{website_url}"

        try:
            URLValidator()(normalized_url)
        except ValidationError:
            raise forms.ValidationError(
                "Podaj poprawny adres strony WWW." if self.ui_language == "pl"
                else "Enter a valid website URL."
            )

        return normalized_url

    def clean_featured_entry_url(self):
        return self._normalize_optional_url(
            self.cleaned_data.get("featured_entry_url"),
            invalid_message_pl="Podaj poprawny link do materiału wiedzy.",
            invalid_message_en="Enter a valid knowledge content URL.",
        )

    def _normalize_optional_url(self, raw_value, *, invalid_message_pl: str, invalid_message_en: str) -> str:
        value = (raw_value or "").strip()
        if not value:
            return ""

        parsed = urlsplit(value)
        normalized = value if parsed.scheme else f"https://{value}"
        try:
            URLValidator()(normalized)
        except ValidationError:
            raise forms.ValidationError(invalid_message_pl if self.ui_language == "pl" else invalid_message_en)
        return normalized

    def clean(self):
        cleaned_data = super().clean()
        
        # Pobierz zaznaczone języki z POST data
        content_languages_str = self.data.get("content_languages", "[]")
        try:
            content_languages = json.loads(content_languages_str)
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
        
        # Waliduj że są wypełnione opisy dla wybranych języków
        for lang_code in content_languages:
            short_field = f"short_description_{lang_code}"
            long_field = f"long_description_{lang_code}"
            
            short_val = self.data.get(short_field, "").strip()
            long_val = self.data.get(long_field, "").strip()
            
            if not short_val or not long_val:
                lang_name = self.language_labels.get(lang_code, lang_code)
                raise forms.ValidationError(
                    f"Krótki i pełny opis dla {lang_name} muszą być wypełnione." if self.ui_language == "pl"
                    else f"Both short and long descriptions for {lang_name} must be filled."
                )

        self._parse_products_by_language(content_languages)

        social_raw = (cleaned_data.get("social_profiles_text") or "").strip()
        if social_raw:
            self._parse_social_profiles_text(social_raw)

        entry_type = (cleaned_data.get("featured_entry_type") or "").strip()
        entry_summary = (cleaned_data.get("featured_entry_summary") or "").strip()
        entry_url = (cleaned_data.get("featured_entry_url") or "").strip()
        if (entry_summary or entry_url) and not entry_type:
            raise forms.ValidationError(
                "Wybierz rodzaj materiału wiedzy o firmie." if self.ui_language == "pl"
                else "Select the knowledge content type."
            )
        
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        
        # Pobierz zaznaczone języki z POST data
        content_languages_str = self.data.get("content_languages", "[]")
        try:
            selected_languages = json.loads(content_languages_str)
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

        instance.descriptions_by_language = description_payload

        # Keep legacy EN/PL columns synchronized for compatibility with old reads.
        if "en" not in instance.content_languages:
            instance.short_description_en = ""
            instance.long_description_en = ""
        if "pl" not in instance.content_languages:
            instance.short_description_pl = ""
            instance.long_description_pl = ""
        
        if commit:
            instance.save()
            self._save_tags(instance, instance.content_languages)
            self._save_social_profiles(instance)
            self._save_products(instance, instance.content_languages)
            self._save_featured_entry(instance)
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
        lines = [line.strip() for line in raw_value.splitlines() if line.strip()]
        parsed: list[dict[str, str]] = []

        for line in lines:
            parts = [part.strip() for part in line.split("|")]
            if not parts or not parts[0]:
                raise forms.ValidationError(
                    "Każdy produkt musi mieć nazwę przed separatorem |."
                    if self.ui_language == "pl"
                    else "Each product must include a name before the | separator."
                )

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

            parsed.append(
                {
                    "name": name,
                    "description": description,
                    "url": normalized_url,
                }
            )

        return parsed

    def _parse_products_by_language(self, selected_languages: list[str]) -> dict[str, list[dict[str, str]]]:
        payload: dict[str, list[dict[str, str]]] = {}
        for language_code in selected_languages:
            raw_products = (self.data.get(f"products_{language_code}") or "").strip()
            payload[language_code] = self._parse_products_text(raw_products) if raw_products else []
        return payload

    def _save_products(self, instance: Organization, selected_languages: list[str]) -> None:
        products_by_language = self._parse_products_by_language(selected_languages)
        instance.products_by_language = products_by_language
        instance.save(update_fields=["products_by_language", "updated_at"])

        # Keep legacy Product rows synchronized from default feed language for backward compatibility.
        instance.products.all().delete()
        primary_products = products_by_language.get(instance.primary_language, [])
        for index, item in enumerate(primary_products):
            product = Product(
                organization=instance,
                name=item["name"],
                product_url=item["url"],
                is_featured=index == 0,
                price_from=None,
            )
            primary_field = f"short_description_{instance.primary_language}"
            if hasattr(product, primary_field):
                setattr(product, primary_field, item["description"])
            fallback_field = "short_description_en" if primary_field != "short_description_en" else "short_description_pl"
            if hasattr(product, fallback_field):
                setattr(product, fallback_field, item["description"])
            product.save()

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
                URLValidator()(normalized)
            except ValidationError:
                raise forms.ValidationError(
                    f"Niepoprawny link social: {candidate}" if self.ui_language == "pl"
                    else f"Invalid social profile URL: {candidate}"
                )

            hostname = (urlsplit(normalized).netloc or "").lower()
            network = None
            for domain, network_code in network_map.items():
                if domain in hostname:
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

    def _save_featured_entry(self, instance: Organization) -> None:
        entry_type = (self.cleaned_data.get("featured_entry_type") or "").strip()
        summary = (self.cleaned_data.get("featured_entry_summary") or "").strip()
        content_url = (self.cleaned_data.get("featured_entry_url") or "").strip()

        featured_entry = instance.content_entries.filter(is_featured=True).order_by("-published_at", "title").first()
        if not any([entry_type, summary, content_url]):
            if featured_entry:
                featured_entry.delete()
            return

        if not featured_entry:
            featured_entry = ContentEntry(organization=instance, is_featured=True)

        featured_entry.entry_type = entry_type or EntryType.UPDATE
        featured_entry.title = self._build_featured_entry_title(instance, featured_entry.entry_type)
        featured_entry.content_url = content_url

        target_languages = list(dict.fromkeys((instance.content_languages or []) + [instance.primary_language]))
        for language_code in target_languages:
            field_name = f"summary_{language_code}"
            if hasattr(featured_entry, field_name):
                setattr(featured_entry, field_name, summary)

        featured_entry.save()

    def _build_featured_entry_title(self, instance: Organization, entry_type: str) -> str:
        type_labels_pl = {
            EntryType.UPDATE: "Aktualnosc firmy",
            EntryType.FAQ: "FAQ firmy",
            EntryType.GUIDE: "Poradnik firmy",
            EntryType.CASE_STUDY: "Case study firmy",
        }
        type_labels_en = {
            EntryType.UPDATE: "Company update",
            EntryType.FAQ: "Company FAQ",
            EntryType.GUIDE: "Company guide",
            EntryType.CASE_STUDY: "Company case study",
        }

        label = (
            type_labels_pl.get(entry_type, "Material wiedzy o firmie")
            if self.ui_language == "pl"
            else type_labels_en.get(entry_type, "Company knowledge content")
        )
        return f"{label}: {instance.name}"[:255]

    @staticmethod
    def _get_allowed_languages_count(organization: Organization | None) -> int:
        """Zwraca ile języków treści ma dostęp na bazie planu subskrypcji."""
        if not organization:
            return 3  # Default dla nowych organizacji
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
                values[lang_code] = (self.data.get(f"products_{lang_code}") or "").strip()
            return values

        stored = getattr(self.instance, "products_by_language", {}) or {}
        for lang_code in selected_languages:
            rows = []
            for item in stored.get(lang_code, []):
                name = (item.get("name") or "").strip()
                description = (item.get("description") or "").strip()
                url = (item.get("url") or "").strip()
                if not name:
                    continue
                if url:
                    rows.append(f"{name} | {description} | {url}")
                else:
                    rows.append(f"{name} | {description}")
            values[lang_code] = "\n".join(rows)
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
            "featured_entry_type",
            "featured_entry_summary",
            "featured_entry_url",
        ]
