from django import forms
from django.utils.translation import get_language

from .models import Organization


POLISH_FIELD_LABELS = {
    "name": "Nazwa firmy",
    "slug": "Slug URL",
    "legal_name": "Pełna nazwa prawna",
    "website_url": "Adres strony WWW",
    "contact_email": "Adres e-mail",
    "phone_number": "Numer telefonu",
    "address_line": "Adres",
    "city": "Miasto",
    "postal_code": "Kod pocztowy",
    "country": "Kraj",
    "primary_language": "Język główny",
    "short_description_en": "Krótki opis (EN)",
    "short_description_pl": "Krótki opis (PL)",
    "long_description_en": "Pełny opis (EN)",
    "long_description_pl": "Pełny opis (PL)",
    "public": "Profil publiczny",
    "allow_ai_indexing": "Zezwól AI na indeksowanie",
}

POLISH_LANGUAGE_CHOICES = [
    ("en", "Angielski"),
    ("pl", "Polski"),
]


class OrganizationForm(forms.ModelForm):
    def __init__(self, *args, language_code: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        active_language = (language_code or get_language() or "en")[:2]
        if active_language != "pl":
            return

        for field_name, label in POLISH_FIELD_LABELS.items():
            if field_name in self.fields:
                self.fields[field_name].label = label

        self.fields["primary_language"].choices = POLISH_LANGUAGE_CHOICES

    class Meta:
        model = Organization
        fields = [
            "name",
            "slug",
            "legal_name",
            "website_url",
            "contact_email",
            "phone_number",
            "address_line",
            "city",
            "postal_code",
            "country",
            "primary_language",
            "short_description_en",
            "short_description_pl",
            "long_description_en",
            "long_description_pl",
            "public",
            "allow_ai_indexing",
        ]
        widgets = {
            "long_description_en": forms.Textarea(attrs={"rows": 5}),
            "long_description_pl": forms.Textarea(attrs={"rows": 5}),
        }
