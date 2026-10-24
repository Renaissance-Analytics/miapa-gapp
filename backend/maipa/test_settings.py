"""Isolated tests only: never use this module to serve an application."""
import os
from unittest.mock import patch

with patch.dict(os.environ, {"MAIPA_SECRET_KEY": "test-only-private-value-" * 4,
                            "MAIPA_LOCAL_DEV": "0", "MAIPA_ALLOWED_HOSTS": "testserver"}):
    from .settings import *  # noqa: F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.PBKDF2PasswordHasher"]
