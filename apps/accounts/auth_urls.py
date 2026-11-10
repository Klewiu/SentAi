from django.contrib.auth import urls as django_auth_urls
from django.contrib.auth.views import LoginView
from django.urls import path

from .forms import UsernameOrEmailAuthenticationForm


urlpatterns = [
    path(
        "login/",
        LoginView.as_view(
            authentication_form=UsernameOrEmailAuthenticationForm,
            template_name="registration/login.html",
        ),
        name="login",
    ),
]
urlpatterns.extend(
    pattern
    for pattern in django_auth_urls.urlpatterns
    if pattern.name != "login"
)
