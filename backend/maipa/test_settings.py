"""Isolated tests only: never use this module to serve an application."""
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

with patch.dict(os.environ, {"MAIPA_SECRET_KEY": "test-only-private-value-" * 4,
                            "MAIPA_LOCAL_DEV": "0", "MAIPA_ALLOWED_HOSTS": "testserver"}):
    from .settings import *  # noqa: F403

# Threaded ACP consumers use separate real connections. Shared-cache in-memory
# SQLite raises SQLITE_LOCKED immediately for an ordinary overlapping reader;
# the installed file-backed database waits for that reader instead. Exercise
# the installed database's concurrency semantics, in a fresh test-only directory.
_test_database = tempfile.TemporaryDirectory(prefix='maipa-test-database-')
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:",
    "TEST": {"NAME": str(Path(_test_database.name) / 'tests.sqlite3')}}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.PBKDF2PasswordHasher"]
