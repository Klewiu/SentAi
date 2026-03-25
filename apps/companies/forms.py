import json
from urllib.parse import urlsplit

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.utils.translation import get_language

from .models import Organization, OrganizationType


LANGUAGE_CHOICES = [
    ("pl", "Polski"),
    ("en", "English"),
    ("es", "Español"),
    ("it", "Italiano"),
    ("de", "Deutsch"),
    ("fr", "Français"),
]

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
    # Wszystkie dostępne języki
    AVAILABLE_LANGUAGES = ["pl", "en", "es", "it", "de", "fr"]
    website_url = forms.CharField(required=False, widget=forms.TextInput())

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
        
        # Usuń pole primary_language i content_languages z formularza
        self.fields.pop("primary_language", None)
        self.fields.pop("content_languages", None)

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

        if "website_url" in self.fields:
            self.fields["website_url"].widget.attrs.update(
                {
                    "type": "text",
                    "inputmode": "url",
                    "autocomplete": "url",
                    "placeholder": "twojadomena.pl" if ui_language == "pl" else "yourdomain.com",
                }
            )
        
        # Na koniec przetłumacz pozostałe pola na PL jeśli trzeba
        if ui_language == "pl":
            for field_name, label in POLISH_FIELD_LABELS.items():
                if field_name in self.fields:
                    self.fields[field_name].label = label

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
        
        # Zapisz opisy wybrane przez użytkownika z POST data
        for lang_code in instance.content_languages:
            short_field = f"short_description_{lang_code}"
            long_field = f"long_description_{lang_code}"
            if hasattr(instance, short_field):
                setattr(instance, short_field, self.data.get(short_field, ""))
            if hasattr(instance, long_field):
                setattr(instance, long_field, self.data.get(long_field, ""))
        
        if commit:
            instance.save()
        return instance

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
        })

    def _build_initial_descriptions(self, selected_languages: list[str]) -> dict:
        descriptions: dict[str, dict[str, str]] = {}
        for lang_code in selected_languages:
            short_field = f"short_description_{lang_code}"
            long_field = f"long_description_{lang_code}"

            if self.is_bound:
                short_value = self.data.get(short_field, "")
                long_value = self.data.get(long_field, "")
            else:
                short_value = getattr(self.instance, short_field, "") if self.instance else ""
                long_value = getattr(self.instance, long_field, "") if self.instance else ""

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
            "phone_number",
            "address_line",
            "city",
            "postal_code",
            "country",
        ]
