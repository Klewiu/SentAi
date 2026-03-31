from django import forms
from django.contrib.auth import get_user_model

from apps.accounts.models import AccountType, USER_PLAN_ORGANIZATION_LIMITS, UserPlanTier


User = get_user_model()


class UserPlanUpdateForm(forms.Form):
    plan_tier = forms.ChoiceField(
        choices=UserPlanTier.choices,
        widget=forms.RadioSelect(attrs={"class": "plan-tier-radio"}),
    )

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
        new_limit = USER_PLAN_ORGANIZATION_LIMITS[selected_tier]
        if current_count > new_limit:
            raise forms.ValidationError(
                f"You currently have {current_count} company pages. "
                f"Please reduce to {new_limit} or fewer before selecting this plan."
            )
        return selected_tier


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


class ProspectClientForm(forms.Form):
    company_name = forms.CharField(max_length=255)
    contact_person = forms.CharField(max_length=255)
    email = forms.EmailField()
    phone = forms.CharField(max_length=20)
    notes = forms.CharField(widget=forms.Textarea, required=False)

    def __init__(self, *args, language_code="en", **kwargs):
        self.language_code = language_code
        super().__init__(*args, **kwargs)

        if self.language_code == "pl":
            self.fields["company_name"].label = "Nazwa firmy"
            self.fields["contact_person"].label = "Osoba kontaktowa"
            self.fields["email"].label = "E-mail"
            self.fields["phone"].label = "Numer telefonu"
            self.fields["notes"].label = "Notatki"
        else:
            self.fields["company_name"].label = "Company name"
            self.fields["contact_person"].label = "Contact person"
            self.fields["email"].label = "Email"
            self.fields["phone"].label = "Phone"
            self.fields["notes"].label = "Notes"

        for field_name in self.fields:
            self.fields[field_name].widget.attrs.update({
                "class": "min-w-0 flex-1 rounded border border-white/10 bg-white/[0.04] px-3 py-2 font-mono text-sm text-slate-100 placeholder-slate-600 outline-none transition focus:border-[#00d4aa]/50 focus:ring-1 focus:ring-[#00d4aa]/30"
            })


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
            "class": "rounded border border-white/10 bg-white/[0.04] px-3 py-2 font-mono text-sm text-slate-100 outline-none transition focus:border-[#00d4aa]/50 focus:ring-1 focus:ring-[#00d4aa]/30"
        })

        self.fields["activity_date"].widget.attrs.update({
            "class": "min-w-0 flex-1 rounded border border-white/10 bg-white/[0.04] px-3 py-2 font-mono text-sm text-slate-100 placeholder-slate-600 outline-none transition focus:border-[#00d4aa]/50 focus:ring-1 focus:ring-[#00d4aa]/30"
        })
        
        self.fields["activity_description"].widget.attrs.update({
            "class": "min-w-0 flex-1 rounded border border-white/10 bg-white/[0.04] px-3 py-2 font-mono text-sm text-slate-100 placeholder-slate-600 outline-none transition focus:border-[#00d4aa]/50 focus:ring-1 focus:ring-[#00d4aa]/30"
        })
