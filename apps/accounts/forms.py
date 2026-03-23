from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm


User = get_user_model()


class UserRegistrationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "company_name", "email", "password1", "password2")

    def __init__(self, *args, **kwargs):
        language_code = kwargs.pop("language_code", "en")
        super().__init__(*args, **kwargs)

        if language_code == "pl":
            self.fields["username"].label = "Nazwa użytkownika"
            self.fields["company_name"].label = "Nazwa firmy"
            self.fields["email"].label = "Email kontaktowy"
            self.fields["password1"].label = "Hasło"
            self.fields["password2"].label = "Powtórz hasło"

        self.fields["username"].widget.attrs.update({"autocomplete": "username"})
        self.fields["company_name"].widget.attrs.update({"autocomplete": "organization"})
        self.fields["email"].widget.attrs.update({"autocomplete": "email"})
        self.fields["password1"].widget.attrs.update({"autocomplete": "new-password"})
        self.fields["password2"].widget.attrs.update({"autocomplete": "new-password"})

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email
