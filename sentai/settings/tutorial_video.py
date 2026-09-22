"""Isolated settings used only to render the local tutorial videos."""

import os

from .dev import *


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ["XOAILA_TUTORIAL_DB"],
    }
}

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

