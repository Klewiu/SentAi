from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordChangeForm,
    PasswordResetForm,
    UserCreationForm,
)
from django_otp.forms import OTPAuthenticationForm


User = get_user_model()


def validate_polish_company_country(value, language_code):
    country = (value or "").strip()
    if country.casefold() not in {"polska", "poland"}:
        raise forms.ValidationError(
            "Aplikacja jest dostępna jedynie dla firm z Polski."
            if language_code == "pl"
            else "The application is available only to companies from Poland."
        )
    return "Polska" if language_code == "pl" else "Poland"


class UsernameOrEmailLoginMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        language_code = getattr(self.request, "LANGUAGE_CODE", "en")
        self.fields["username"].label = (
            "Nazwa użytkownika lub e-mail"
            if language_code == "pl"
            else "Username or email"
        )
        self.fields["username"].widget.attrs.update({
            "autocomplete": "username",
            "placeholder": (
                "Nazwa użytkownika lub e-mail"
                if language_code == "pl"
                else "Username or email"
            ),
        })

    wrong_password = False

    def clean(self):
        login = (self.cleaned_data.get("username") or "").strip()
        if login:
            user = User.objects.filter(email__iexact=login).first()
            if user is None:
                user = User.objects.filter(username__iexact=login).first()
            if user is None:
                language_code = getattr(self.request, "LANGUAGE_CODE", "en")
                raise forms.ValidationError(
                    "Podany login lub e-mail nie istnieje."
                    if language_code == "pl"
                    else "The username or email does not exist.",
                    code="unknown_account",
                )
            if "@" in login:
                self.cleaned_data["username"] = user.get_username()
        try:
            return super().clean()
        except forms.ValidationError as error:
            if getattr(error, "code", None) == "invalid_login":
                self.wrong_password = True
            raise


class UsernameOrEmailAuthenticationForm(UsernameOrEmailLoginMixin, AuthenticationForm):
    pass


class UsernameOrEmailOTPAuthenticationForm(UsernameOrEmailLoginMixin, OTPAuthenticationForm):
    pass


class UsernameOrEmailPasswordResetForm(PasswordResetForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"] = forms.CharField(max_length=254)

    def clean_email(self):
        identifier = self.cleaned_data["email"].strip()
        user = User.objects.filter(email__iexact=identifier).first()
        if user is None:
            user = User.objects.filter(username__iexact=identifier).first()
        if user is not None:
            return user.email
        return identifier if "@" in identifier else ""


class UserRegistrationForm(UserCreationForm):
    country = forms.ChoiceField(required=True)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "company_name", "email", "country", "password1", "password2")

    def __init__(self, *args, **kwargs):
        language_code = kwargs.pop("language_code", "en")
        self.language_code = language_code
        super().__init__(*args, **kwargs)

        self.fields["company_name"].required = True
        self.fields["country"].required = True
        self.fields["country"].choices = [
            ("", "Wybierz kraj" if language_code == "pl" else "Select a country"),
            ("Poland", "Polska" if language_code == "pl" else "Poland"),
        ]
        self.fields["country"].error_messages.update({
            "required": "Wybierz kraj." if language_code == "pl" else "Select a country.",
            "invalid_choice": (
                "Wybierz kraj z dostępnej listy."
                if language_code == "pl"
                else "Select a country from the available options."
            ),
        })

        if language_code == "pl":
            self.fields["username"].label = "Nazwa użytkownika"
            self.fields["company_name"].label = "Nazwa firmy"
            self.fields["email"].label = "Email kontaktowy"
            self.fields["country"].label = "Kraj"
            self.fields["password1"].label = "Hasło"
            self.fields["password2"].label = "Powtórz hasło"
        else:
            self.fields["country"].label = "Country"

        self.fields["username"].widget.attrs.update({"autocomplete": "username"})
        self.fields["company_name"].widget.attrs.update({"autocomplete": "organization"})
        self.fields["email"].widget.attrs.update({"autocomplete": "email"})
        self.fields["password1"].widget.attrs.update({"autocomplete": "new-password"})
        self.fields["password2"].widget.attrs.update({"autocomplete": "new-password"})

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email

    def clean_country(self):
        if not self.cleaned_data.get("country") and not self.fields["country"].required:
            return ""
        return validate_polish_company_country(
            self.cleaned_data.get("country"),
            self.language_code,
        )


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("username", "company_name", "email", "country")

    def __init__(self, *args, **kwargs):
        language_code = kwargs.pop("language_code", "en")
        self.language_code = language_code
        require_business_details = kwargs.pop("require_business_details", False)
        super().__init__(*args, **kwargs)
        if require_business_details:
            self.fields["company_name"].required = True
            self.fields["country"].required = True
        if language_code == "pl":
            self.fields["username"].label = "Nazwa użytkownika"
            self.fields["company_name"].label = "Nazwa firmy"
            self.fields["email"].label = "E-mail kontaktowy"
            self.fields["country"].label = "Kraj"
        else:
            self.fields["country"].label = "Country"

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        qs = User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email

    def clean_country(self):
        if not self.cleaned_data.get("country") and not self.fields["country"].required:
            return ""
        return validate_polish_company_country(
            self.cleaned_data.get("country"),
            self.language_code,
        )


class ProfilePasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        language_code = kwargs.pop("language_code", "en")
        super().__init__(*args, **kwargs)
        if language_code == "pl":
            self.fields["old_password"].label = "Aktualne hasło"
            self.fields["new_password1"].label = "Nowe hasło"
            self.fields["new_password2"].label = "Powtórz nowe hasło"
