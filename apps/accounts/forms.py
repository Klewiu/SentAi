from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordChangeForm, UserCreationForm


User = get_user_model()


class UserRegistrationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "company_name", "email", "country", "password1", "password2")

    def __init__(self, *args, **kwargs):
        language_code = kwargs.pop("language_code", "en")
        super().__init__(*args, **kwargs)

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
        self.fields["country"].widget.attrs.update({"autocomplete": "country-name"})
        self.fields["password1"].widget.attrs.update({"autocomplete": "new-password"})
        self.fields["password2"].widget.attrs.update({"autocomplete": "new-password"})

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("username", "company_name", "email", "country")

    def __init__(self, *args, **kwargs):
        language_code = kwargs.pop("language_code", "en")
        super().__init__(*args, **kwargs)
        if language_code == "pl":
            self.fields["username"].label = "Nazwa użytkownika"
            self.fields["company_name"].label = "Nazwa firmy"
            self.fields["email"].label = "E-mail kontaktowy"
            self.fields["country"].label = "Kraj"
        else:
            self.fields["country"].label = "Country"

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        qs = User.objects.filter(email=email).exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email


class ProfilePasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        language_code = kwargs.pop("language_code", "en")
        super().__init__(*args, **kwargs)
        if language_code == "pl":
            self.fields["old_password"].label = "Aktualne hasło"
            self.fields["new_password1"].label = "Nowe hasło"
            self.fields["new_password2"].label = "Powtórz nowe hasło"
