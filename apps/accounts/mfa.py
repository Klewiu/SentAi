import base64
import io
import secrets
import time

import django_otp
import qrcode
import qrcode.image.svg
from django import forms
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache
from django_otp import devices_for_user
from django_otp.forms import OTPTokenForm
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice
from .models import User
from .views import MFALoginView


@method_decorator(never_cache, name="dispatch")
class FriendlyMFALoginView(MFALoginView):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            if not (request.user.is_staff or request.user.is_superuser) or request.user.is_verified():
                return redirect("dashboard:home")
            if not list(devices_for_user(request.user, confirmed=True)):
                return redirect("accounts:mfa-setup")
        return super().dispatch(request, *args, **kwargs)

    def get_form_class(self):
        return OTPTokenForm if self.request.user.is_authenticated else self.authentication_form

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if self.request.user.is_authenticated:
            kwargs["user"] = self.request.user
        return kwargs

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields["otp_token"].label = "Verification code"
        form.fields["otp_token"].required = True
        form.fields["otp_token"].widget.attrs.update(autocomplete="one-time-code", autofocus=True)
        form.fields["otp_device"].label = "Verification method"
        choices = list(getattr(form.fields["otp_device"], "choices", []))
        if choices:
            form.fields["otp_device"].initial = next((key for key, _ in choices if key.startswith("otp_totp.")), choices[0][0])
        if len(choices) == 1:
            form.fields["otp_device"].initial = choices[0][0]
            form.fields["otp_device"].widget = forms.HiddenInput()
        return form

    def form_valid(self, form):
        if self.request.user.is_authenticated:
            django_otp.login(self.request, form.get_user().otp_device)
            return redirect("dashboard:home")
        return super().form_valid(form)


@method_decorator(never_cache, name="dispatch")
class MFASetupView(LoginRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not (request.user.is_staff or request.user.is_superuser):
            return redirect("dashboard:home")
        return super().dispatch(request, *args, **kwargs)

    def pending_device(self, request):
        pending = request.session.get("mfa_enrollment", {})
        if time.time() - pending.get("created", 0) > 600 or pending.get("auth") != request.user.get_session_auth_hash():
            request.session.pop("mfa_enrollment", None)
            return None
        return TOTPDevice.objects.filter(pk=pending.get("device"), user=request.user, confirmed=False).first()

    def page(self, request, error=""):
        device = self.pending_device(request)
        context = {"error": error}
        if device:
            output = io.BytesIO()
            qrcode.make(device.config_url, image_factory=qrcode.image.svg.SvgPathImage).save(output)
            context.update(qr="data:image/svg+xml;base64," + base64.b64encode(output.getvalue()).decode(), setup_key=base64.b32encode(device.bin_key).decode())
        return render(request, "registration/mfa_setup.html", context)

    def get(self, request):
        if list(devices_for_user(request.user, confirmed=True)):
            return redirect("accounts:mfa")
        return self.page(request)

    @transaction.atomic
    def post(self, request):
        user = User.objects.select_for_update().get(pk=request.user.pk)
        if list(devices_for_user(user, confirmed=True)):
            return redirect("accounts:mfa")
        device = self.pending_device(request)
        if request.POST.get("action") == "start":
            if not user.check_password(request.POST.get("password", "")):
                return self.page(request, "Check your account password and try again.")
            if not device:
                device = TOTPDevice.objects.create(user=user, name="Authenticator app", confirmed=False)
                request.session["mfa_enrollment"] = {"device": device.pk, "created": time.time(), "auth": user.get_session_auth_hash()}
            return self.page(request)
        if not device:
            return self.page(request, "Setup expired. Confirm your password to start again.")
        if not device.verify_token(request.POST.get("code", "").strip()):
            return self.page(request, "That code did not verify. Use the latest code from your authenticator and try again.")
        device.confirmed = True
        device.save(update_fields=["confirmed"])
        codes = [secrets.token_hex(8) for _ in range(10)]
        recovery = StaticDevice.objects.create(user=user, name="Recovery code")
        StaticToken.objects.bulk_create([StaticToken(device=recovery, token=code) for code in codes])
        request.session.pop("mfa_enrollment", None)
        django_otp.login(request, device)
        return render(request, "registration/mfa_setup.html", {"recovery_codes": codes})
