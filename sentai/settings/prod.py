from .base import *
from django.core.exceptions import ImproperlyConfigured
from urllib.parse import urlsplit

DEBUG = False
if len(SECRET_KEY) < 50 or len(set(SECRET_KEY)) < 5 or SECRET_KEY.startswith("django-insecure-"):
    raise ImproperlyConfigured("Set a strong DJANGO_SECRET_KEY before starting production.")
if urlsplit(SITE_BASE_URL).scheme != "https":
    raise ImproperlyConfigured("Production SITE_BASE_URL must use HTTPS.")
if STRIPE_SECRET_KEY and not STRIPE_WEBHOOK_SECRET:
    raise ImproperlyConfigured("Stripe requires STRIPE_WEBHOOK_SECRET in production.")
if EMAIL_BACKEND.endswith("console.EmailBackend"):
    raise ImproperlyConfigured("Configure a delivery email backend for production.")
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
