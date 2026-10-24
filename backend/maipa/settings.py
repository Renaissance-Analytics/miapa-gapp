"""Fail-closed Django foundation. Local SQLite is development storage only."""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parents[2]
FRONTEND_DIST = BASE_DIR / "frontend/dist"
ACP_RUNTIME_ROOT = BASE_DIR / "backend/agent"
ACP_CHECK_ROOT = BASE_DIR / "runtime/acp-checks"
ACP_NODE_EXECUTABLE = ""  # Resolved from the server PATH, never HTTP input.
SECRET_KEY = os.environ.get("MAIPA_SECRET_KEY", "")
if len(SECRET_KEY) < 50:
    raise ImproperlyConfigured("Set MAIPA_SECRET_KEY to a private random value of at least 50 characters.")
LOCAL_DEV = os.environ.get("MAIPA_LOCAL_DEV") == "1"
DEBUG = False
ALLOWED_HOSTS = [host.strip() for host in os.environ.get("MAIPA_ALLOWED_HOSTS", "").split(",") if host.strip()]
if not ALLOWED_HOSTS and LOCAL_DEV:
    ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]
if not ALLOWED_HOSTS or any("*" in host or "://" in host or "/" in host for host in ALLOWED_HOSTS):
    raise ImproperlyConfigured("Set exact MAIPA_ALLOWED_HOSTS names; wildcard hosts are forbidden.")

INSTALLED_APPS = ["django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions",
                  "backend.maipa.workspace.apps.WorkspaceConfig",
                  "backend.maipa.intake.apps.IntakeConfig",
                  "backend.maipa.agent_accounts.apps.AgentAccountsConfig"]
MIDDLEWARE = [
    "backend.maipa.middleware.ApiCachePolicyMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]
ROOT_URLCONF = "backend.maipa.urls"
ASGI_APPLICATION = "backend.maipa.asgi.application"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "runtime/maipa.sqlite3"}}
USE_TZ = True
TIME_ZONE = "UTC"
LANGUAGE_CODE = "en-us"
APPEND_SLASH = False

SESSION_COOKIE_SECURE = not LOCAL_DEV
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = not LOCAL_DEV
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
# Genie terminates HTTPS before the explicit local loopback server. Trust only
# this application's exact public origin; never trust arbitrary proxy headers.
CSRF_TRUSTED_ORIGINS = ["https://maipa.gen"] if LOCAL_DEV and "maipa.gen" in ALLOWED_HOSTS else []
CSRF_FAILURE_VIEW = "backend.maipa.api.csrf_failure"
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_HSTS_SECONDS = 0  # TLS/proxy/HSTS deployment policy is not yet accepted.
DATA_UPLOAD_MAX_MEMORY_SIZE = 1_048_576
FILE_UPLOAD_MAX_MEMORY_SIZE = 1_048_576

# Avoid Django request/error logging of URLs or user input in this foundation.
# A structured, redacted audit/logging policy is separate application work.
LOGGING = {"version": 1, "disable_existing_loggers": False,
           "handlers": {"discard": {"class": "logging.NullHandler"}},
           "loggers": {"django.request": {"handlers": ["discard"], "propagate": False},
                       "django.security": {"handlers": ["discard"], "propagate": False}}}
