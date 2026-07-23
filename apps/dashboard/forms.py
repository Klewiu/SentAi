import re

from django import forms
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from django.db.models import Q
from django.contrib.auth import get_user_model

from apps.accounts.models import AccountType, USER_PLAN_ORGANIZATION_LIMITS, UserPlanTier
from apps.billing.models import BillingCurrency, BillingCustomerType, BillingInvoice, BillingPayment, BillingPlanPrice, BillingProfile


User = get_user_model()


EU_VAT_COUNTRY_CODES = {
    "AT",
    "BE",
    "BG",
    "CY",
    "CZ",
    "DE",
    "DK",
    "EE",
    "EL",
    "ES",
    "FI",
    "FR",
    "HR",
    "HU",
    "IE",
    "IT",
    "LT",
    "LU",
    "LV",
    "MT",
    "NL",
    "PL",
    "PT",
    "RO",
    "SE",
    "SI",
    "SK",
}


def normalize_vat_id(value: str, country: str) -> str:
    normalized = re.sub(r"[\s.\-_/]", "", value or "").upper()
    country = (country or "").upper()
    if country and normalized and not normalized.startswith(country):
        if len(normalized) >= 2 and normalized[:2].isalpha():
            return normalized
        return f"{country}{normalized}"
    return normalized


def is_valid_polish_nip(vat_id: str) -> bool:
    number = vat_id[2:] if vat_id.startswith("PL") else vat_id
    if not re.fullmatch(r"\d{10}", number):
        return False
    weights = [6, 5, 7, 2, 3, 4, 5, 6, 7]
    checksum = sum(int(number[index]) * weights[index] for index in range(9)) % 11
    return checksum != 10 and checksum == int(number[9])


class RegisteredClientChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        company_name = (obj.company_name or "").strip()
        if company_name:
            return company_name

        full_name = (obj.get_full_name() or "").strip()
        if full_name:
            return full_name

        return obj.username


class UserPlanUpdateForm(forms.Form):
    PRO_MANUAL = "PRO_MANUAL"
    plan_tier = forms.ChoiceField(
        choices=[*UserPlanTier.choices, (PRO_MANUAL, "Pro Manual")],
        widget=forms.RadioSelect(attrs={"class": "plan-tier-radio"}),
    )
    billing_currency = forms.ChoiceField(
        choices=BillingCurrency.choices,
        initial=BillingCurrency.PLN,
        required=False,
        widget=forms.HiddenInput,
    )
    subscription_terms_accepted = forms.BooleanField(required=False)

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        if self.user and not self.is_bound:
            self.fields["plan_tier"].initial = self.user.plan_tier

    def clean_plan_tier(self):
        selected_tier = self.cleaned_data["plan_tier"]
        if not self.user or self.user.is_superuser:
            return selected_tier

        current_count = self.user.organizations.count()
        effective_tier = UserPlanTier.PRO if selected_tier == self.PRO_MANUAL else selected_tier
        new_limit = USER_PLAN_ORGANIZATION_LIMITS[effective_tier]
        if current_count > new_limit:
            raise forms.ValidationError(
                f"You currently have {current_count} company pages. "
                f"Please reduce to {new_limit} or fewer before selecting this plan."
            )
        return selected_tier

    def clean_billing_currency(self):
        return (self.cleaned_data.get("billing_currency") or BillingCurrency.PLN).lower()

    def clean(self):
        cleaned_data = super().clean()
        selected_tier = cleaned_data.get("plan_tier")
        terms_accepted = cleaned_data.get("subscription_terms_accepted")

        if selected_tier in {UserPlanTier.PLUS, UserPlanTier.PRO} and not terms_accepted:
            self.add_error(
                "subscription_terms_accepted",
                "You must accept the subscription terms before continuing to payment.",
            )

        return cleaned_data


class SellerCreateForm(forms.Form):
    username = forms.CharField(max_length=150)
    email = forms.EmailField()
    password1 = forms.CharField(widget=forms.PasswordInput)
    password2 = forms.CharField(widget=forms.PasswordInput)

    def __init__(self, *args, language_code="en", **kwargs):
        self.language_code = language_code
        super().__init__(*args, **kwargs)

        if self.language_code == "pl":
            self.fields["username"].label = "Login"
            self.fields["email"].label = "E-mail"
            self.fields["password1"].label = "Hasło"
            self.fields["password2"].label = "Powtórz hasło"
        else:
            self.fields["username"].label = "Login"
            self.fields["email"].label = "Email"
            self.fields["password1"].label = "Password"
            self.fields["password2"].label = "Confirm password"

        self.fields["username"].widget.attrs.update({"autocomplete": "username"})
        self.fields["email"].widget.attrs.update({"autocomplete": "email"})
        self.fields["password1"].widget.attrs.update({"autocomplete": "new-password"})
        self.fields["password2"].widget.attrs.update({"autocomplete": "new-password"})

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username=username).exists():
            if self.language_code == "pl":
                raise forms.ValidationError("Użytkownik z takim loginem już istnieje.")
            raise forms.ValidationError("A user with this login already exists.")
        return username

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")

        if password1 and password2 and password1 != password2:
            if self.language_code == "pl":
                raise forms.ValidationError("Hasła nie są takie same.")
            raise forms.ValidationError("Passwords do not match.")

        return cleaned_data

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email=email).exists():
            if self.language_code == "pl":
                raise forms.ValidationError("Użytkownik z takim adresem e-mail już istnieje.")
            raise forms.ValidationError("A user with this email already exists.")
        return email

    def save(self):
        username = self.cleaned_data["username"]
        email = self.cleaned_data["email"]
        password = self.cleaned_data["password1"]

        return User.objects.create_user(
            username=username,
            email=email,
            password=password,
            account_type=AccountType.STAFF,
            is_active=True,
        )


class BillingPlanPriceForm(forms.ModelForm):
    amount = forms.DecimalField(
        decimal_places=2,
        min_value=Decimal("0.01"),
        label="Amount",
        help_text="Enter the amount like Stripe, e.g. 49.00 PLN or 12.00 EUR.",
    )

    class Meta:
        model = BillingPlanPrice
        fields = ("tier", "stripe_price_id", "amount", "currency", "interval", "active_for_new_customers", "notes")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and not self.is_bound:
            self.initial["amount"] = Decimal(self.instance.amount) / Decimal("100")

    def clean_amount(self):
        value = self.cleaned_data["amount"]
        try:
            smallest_unit_amount = (value * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        except (InvalidOperation, TypeError):
            raise forms.ValidationError("Enter a valid amount, e.g. 49.00.")
        return int(smallest_unit_amount)

    def clean(self):
        cleaned_data = super().clean()
        tier = cleaned_data.get("tier")
        active = cleaned_data.get("active_for_new_customers")

        currency = cleaned_data.get("currency")

        if tier and currency and active:
            qs = BillingPlanPrice.objects.filter(
                tier=tier,
                currency=currency,
                active_for_new_customers=True,
            )
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError(
                    "This plan already has an active price for new customers. "
                    "Archive the current active price for this currency before activating another one."
                )

        return cleaned_data


class BillingPaymentInvoiceForm(forms.ModelForm):
    class Meta:
        model = BillingPayment
        fields = (
            "invoice_issued",
            "invoice_issued_at",
            "invoice_number",
            "invoice_document",
            "invoice_sent",
            "invoice_sent_at",
        )
        widgets = {
            "invoice_issued_at": forms.DateInput(attrs={"type": "date"}),
            "invoice_sent_at": forms.DateInput(attrs={"type": "date"}),
            "invoice_number": forms.TextInput(attrs={"placeholder": "Accounting invoice number"}),
        }

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("invoice_issued"):
            if not cleaned_data.get("invoice_issued_at"):
                self.add_error("invoice_issued_at", "Enter the date when the invoice was issued.")
            if not (cleaned_data.get("invoice_number") or "").strip():
                self.add_error("invoice_number", "Enter the invoice number.")
            if not cleaned_data.get("invoice_document") and not self.instance.invoice_document:
                self.add_error("invoice_document", "Upload the invoice PDF.")
        if cleaned_data.get("invoice_sent"):
            if not cleaned_data.get("invoice_issued"):
                self.add_error("invoice_sent", "An invoice must be issued before it can be marked as sent.")
            if not cleaned_data.get("invoice_sent_at"):
                self.add_error("invoice_sent_at", "Enter the date when the invoice was sent.")
        return cleaned_data


class BillingInvoiceForm(forms.ModelForm):
    class Meta:
        model = BillingInvoice
        fields = ("issued_at", "sent_at", "invoice_number", "document")
        widgets = {
            "issued_at": forms.DateInput(attrs={"type": "date"}),
            "sent_at": forms.DateInput(attrs={"type": "date"}),
            "invoice_number": forms.TextInput(attrs={"placeholder": "Invoice number"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sent_at"].required = True


class BillingProfileForm(forms.ModelForm):
    class Meta:
        model = BillingProfile
        fields = (
            "company_name",
            "tax_id",
            "street",
            "postal_code",
            "city",
            "country",
            "invoice_email",
        )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        if self.user and not self.is_bound:
            self.fields["invoice_email"].initial = self.user.email
            if getattr(self.user, "company_name", ""):
                self.fields["company_name"].initial = self.user.company_name
        self.fields["country"].help_text = "Use PL for Polish customers. Other countries will use EUR checkout."
        self.fields["tax_id"].label = "VAT ID"
        self.fields["tax_id"].help_text = "Use the country prefix, e.g. PL5260250995. If omitted, we add the selected country."
        self.fields["tax_id"].required = True
        self.fields["company_name"].required = True

    def clean_country(self):
        return self.cleaned_data["country"].strip().upper()

    def clean(self):
        cleaned_data = super().clean()
        company_name = (cleaned_data.get("company_name") or "").strip()
        country = (cleaned_data.get("country") or "").strip().upper()
        tax_id = normalize_vat_id(cleaned_data.get("tax_id") or "", country)

        cleaned_data["customer_type"] = BillingCustomerType.COMPANY
        if not company_name:
            self.add_error("company_name", "Company name is required for company billing.")
        if not tax_id:
            self.add_error("tax_id", "VAT ID is required for billing.")
        elif country and tax_id[:2].isalpha() and tax_id[:2] != country:
            self.add_error("tax_id", "VAT ID country prefix must match the selected billing country.")
        elif country == "PL" and not is_valid_polish_nip(tax_id):
            self.add_error(
                "tax_id",
                "Enter a valid Polish VAT ID/NIP. It must contain 10 digits and pass the NIP checksum, e.g. PL5260250995.",
            )
        elif country in EU_VAT_COUNTRY_CODES and not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{2,13}", tax_id):
            self.add_error("tax_id", "Enter a valid EU VAT ID with country prefix, e.g. DE123456789.")
        elif not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{2,20}", tax_id):
            self.add_error("tax_id", "Enter a valid VAT ID with country prefix.")
        else:
            cleaned_data["tax_id"] = tax_id

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.customer_type = BillingCustomerType.COMPANY
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class ProspectClientForm(forms.Form):
    company_name = forms.CharField(max_length=255)
    contact_person = forms.CharField(max_length=255)
    email = forms.EmailField()
    phone = forms.CharField(max_length=20)
    website_url = forms.URLField(required=False)
    notes = forms.CharField(widget=forms.Textarea, required=False)
    registered_client = RegisteredClientChoiceField(queryset=User.objects.none(), required=False)

    def __init__(self, *args, language_code="en", **kwargs):
        self.language_code = language_code
        super().__init__(*args, **kwargs)

        if self.language_code == "pl":
            self.fields["company_name"].label = "Nazwa firmy"
            self.fields["contact_person"].label = "Osoba kontaktowa"
            self.fields["email"].label = "E-mail"
            self.fields["phone"].label = "Numer telefonu"
            self.fields["website_url"].label = "Strona WWW"
            self.fields["notes"].label = "Notatki"
            self.fields["registered_client"].label = "Powiązany klient"
        else:
            self.fields["company_name"].label = "Company name"
            self.fields["contact_person"].label = "Contact person"
            self.fields["email"].label = "Email"
            self.fields["phone"].label = "Phone"
            self.fields["website_url"].label = "Website"
            self.fields["notes"].label = "Notes"
            self.fields["registered_client"].label = "Linked client"

        self.fields["registered_client"].queryset = (
            User.objects.filter(
                account_type=AccountType.CLIENT,
                is_superuser=False,
                attributed_prospect__isnull=True,
            )
            .order_by("email")
        )
        self.fields["registered_client"].empty_label = (
            "-- brak --" if self.language_code == "pl" else "-- none --"
        )

        for field_name in self.fields:
            self.fields[field_name].widget.attrs.update({
                "class": "min-w-0 w-full rounded border border-[#d9d9d9] bg-white px-3 py-2 font-mono text-sm text-[#353535] placeholder-[#353535]/30 outline-none transition focus:border-[#5ca197] focus:ring-1 focus:ring-[#5ca197]/30"
            })

    def clean_website_url(self):
        value = self.cleaned_data.get("website_url", "").strip()
        if value and not value.startswith(("http://", "https://")):
            value = "https://" + value
        return value


class ProspectActivityForm(forms.Form):
    activity_type = forms.ChoiceField(
        choices=[
            ("call", "Telefon"),
            ("email", "Email"),
            ("meeting", "Spotkanie"),
        ]
    )
    activity_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    activity_description = forms.CharField(widget=forms.Textarea)

    def __init__(self, *args, language_code="en", **kwargs):
        from django.utils import timezone
        self.language_code = language_code
        super().__init__(*args, **kwargs)

        if self.language_code == "pl":
            self.fields["activity_type"].label = "Typ aktywności"
            self.fields["activity_date"].label = "Data aktywności"
            self.fields["activity_description"].label = "Notatka"
            self.fields["activity_type"].choices = [
                ("call", "☎️ Telefon"),
                ("email", "✉️ Email"),
                ("meeting", "🤝 Spotkanie"),
            ]
        else:
            self.fields["activity_type"].label = "Activity type"
            self.fields["activity_date"].label = "Activity date"
            self.fields["activity_description"].label = "Note"
            self.fields["activity_type"].choices = [
                ("call", "☎️ Phone call"),
                ("email", "✉️ Email"),
                ("meeting", "🤝 Meeting"),
            ]

        self.fields["activity_date"].initial = timezone.now().date()

        self.fields["activity_type"].widget.attrs.update({
            "class": "rounded border border-[#d9d9d9] bg-white px-3 py-2 font-mono text-sm text-[#353535] outline-none transition focus:border-[#5ca197] focus:ring-1 focus:ring-[#5ca197]/30"
        })

        self.fields["activity_date"].widget.attrs.update({
            "class": "min-w-0 flex-1 rounded border border-[#d9d9d9] bg-white px-3 py-2 font-mono text-sm text-[#353535] placeholder-[#353535]/30 outline-none transition focus:border-[#5ca197] focus:ring-1 focus:ring-[#5ca197]/30"
        })

        self.fields["activity_description"].widget.attrs.update({
            "class": "min-w-0 flex-1 rounded border border-[#d9d9d9] bg-white px-3 py-2 font-mono text-sm text-[#353535] placeholder-[#353535]/30 outline-none transition focus:border-[#5ca197] focus:ring-1 focus:ring-[#5ca197]/30"
        })


class ProspectLinkClientForm(forms.Form):
    registered_client = RegisteredClientChoiceField(queryset=User.objects.none())

    def __init__(self, *args, language_code="en", prospect=None, **kwargs):
        self.language_code = language_code
        self.prospect = prospect
        super().__init__(*args, **kwargs)

        current_client_id = None
        if self.prospect and self.prospect.registered_client_id:
            current_client_id = self.prospect.registered_client_id

        self.fields["registered_client"].queryset = (
            User.objects.filter(
                account_type=AccountType.CLIENT,
                is_superuser=False,
            )
            .filter(Q(attributed_prospect__isnull=True) | Q(pk=current_client_id))
            .order_by("email")
        )

        if current_client_id and not self.is_bound:
            self.fields["registered_client"].initial = current_client_id

        if self.language_code == "pl":
            self.fields["registered_client"].label = "Powiąż z zarejestrowanym klientem"
        else:
            self.fields["registered_client"].label = "Link to registered client"

        self.fields["registered_client"].widget.attrs.update(
            {
                "class": "min-w-0 w-full rounded border border-[#d9d9d9] bg-white px-3 py-2 font-mono text-sm text-[#353535] placeholder-[#353535]/30 outline-none transition focus:border-[#5ca197] focus:ring-1 focus:ring-[#5ca197]/30"
            }
        )
