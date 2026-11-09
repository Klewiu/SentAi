import os
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

from .base import *

DEBUG = False
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = os.getenv("EMAIL_HOST", "mail92.mydevil.net")
EMAIL_PORT = env_int("EMAIL_PORT", 587)
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "kontakt@xoaila.pl")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "kontakt@xoaila.pl")
REGISTRATION_EMAIL_VERIFICATION_REQUIRED = env_bool(
    "REGISTRATION_EMAIL_VERIFICATION_REQUIRED",
    True,
)

if len(SECRET_KEY) < 50 or len(set(SECRET_KEY)) < 5 or SECRET_KEY.startswith("django-insecure-"):
    raise ImproperlyConfigured("Set a strong DJANGO_SECRET_KEY before starting production.")
if urlsplit(SITE_BASE_URL).scheme != "https":
    raise ImproperlyConfigured("Production SITE_BASE_URL must use HTTPS.")
if (SUBSCRIPTION_BILLING_ENABLED or BLIK_PAYMENTS_ENABLED) and STRIPE_SECRET_KEY and not STRIPE_WEBHOOK_SECRET:
    raise ImproperlyConfigured("Stripe requires STRIPE_WEBHOOK_SECRET in production.")
if EMAIL_BACKEND.endswith("console.EmailBackend"):
    raise ImproperlyConfigured("Configure a delivery email backend for production.")
if not EMAIL_HOST_USER or not EMAIL_HOST_PASSWORD:
    raise ImproperlyConfigured("Set EMAIL_HOST_USER and EMAIL_HOST_PASSWORD for production email.")
if EMAIL_USE_TLS and EMAIL_USE_SSL:
    raise ImproperlyConfigured("Choose either EMAIL_USE_TLS or EMAIL_USE_SSL, not both.")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_HSTS_PRELOAD = env_bool("DJANGO_HSTS_PRELOAD", False)
# Enable only behind a proxy that strips and sets this header itself.
if env_bool("DJANGO_TRUST_PROXY", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

ADMIN_MFA_REQUIRED = True
