"""Isolated test configuration; never uses developer databases or payment keys."""
from .base import *

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
STRIPE_SECRET_KEY = ""
STRIPE_WEBHOOK_SECRET = ""
ALLOWED_HOSTS = ["testserver", "localhost"]
STORAGES = {"default": {"BACKEND": "django.core.files.storage.InMemoryStorage"}, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
AUTH_RATE_LIMIT = 10000

STORAGES["private"] = {"BACKEND": "django.core.files.storage.InMemoryStorage"}

ADMIN_MFA_REQUIRED = False
