from django.contrib.auth import urls as django_auth_urls
from django.contrib.auth.views import (
    LoginView,
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.urls import path, reverse_lazy

from .forms import (
    UsernameOrEmailAuthenticationForm,
    UsernameOrEmailPasswordResetForm,
)


class LocalizedPasswordResetView(PasswordResetView):
    def form_valid(self, form):
        self.extra_email_context = {
            **(self.extra_email_context or {}),
            "language_code": self.request.LANGUAGE_CODE,
        }
        return super().form_valid(form)


urlpatterns = [
    path(
        "login/",
        LoginView.as_view(
            authentication_form=UsernameOrEmailAuthenticationForm,
            template_name="registration/login.html",
        ),
        name="login",
    ),
    path(
        "password_reset/",
        LocalizedPasswordResetView.as_view(
            form_class=UsernameOrEmailPasswordResetForm,
            template_name="registration/password_reset_form.html",
            email_template_name="emails/password_reset_email.txt",
            html_email_template_name="emails/password_reset_email.html",
            subject_template_name="emails/password_reset_subject.txt",
            success_url=reverse_lazy("password_reset_done"),
        ),
        name="password_reset",
    ),
    path(
        "password_reset/done/",
        PasswordResetDoneView.as_view(
            template_name="registration/password_reset_done.html",
        ),
        name="password_reset_done",
    ),
    path(
        "reset/<uidb64>/<token>/",
        PasswordResetConfirmView.as_view(
            template_name="registration/password_reset_confirm.html",
            success_url=reverse_lazy("password_reset_complete"),
        ),
        name="password_reset_confirm",
    ),
    path(
        "reset/done/",
        PasswordResetCompleteView.as_view(
            template_name="registration/password_reset_complete.html",
        ),
        name="password_reset_complete",
    ),
]
urlpatterns.extend(
    pattern
    for pattern in django_auth_urls.urlpatterns
    if pattern.name not in {
        "login",
        "password_reset",
        "password_reset_done",
        "password_reset_confirm",
        "password_reset_complete",
    }
)
