"""Bounded JSON password login using Django sessions and server-owned membership."""
from datetime import timedelta
import json

from django.contrib.auth import authenticate, login
from django.contrib.auth.base_user import AbstractBaseUser
from django.db import transaction
from django.db.models import F
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables

from backend.maipa.api import error, method_not_allowed
from .models import LoginBudget
from .policy import PERMISSIONS, authorize, membership


def csrf_bootstrap(request):
    if request.method != "GET":
        return method_not_allowed("GET")
    return JsonResponse({"csrf_token": get_token(request)})


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _claim_budget(username):
    fingerprint = salted_hmac("maipa-login-budget-v1", username, algorithm="sha256").hexdigest()
    now = timezone.now()
    with transaction.atomic():
        # No plaintext username/IP/password is retained in this budget table.
        LoginBudget.objects.filter(started_at__lt=now - timedelta(days=1)).delete()
        LoginBudget.objects.get_or_create(fingerprint=fingerprint)
        LoginBudget.objects.filter(fingerprint=fingerprint, started_at__lte=now - timedelta(minutes=15)).update(started_at=now, attempts=0)
        claimed = LoginBudget.objects.filter(fingerprint=fingerprint, attempts__lt=5).update(attempts=F("attempts") + 1)
    return fingerprint if claimed else None


@sensitive_post_parameters()
@sensitive_variables("body", "value", "username", "password", "user")
def session_login(request):
    if request.method != "POST":
        return method_not_allowed("POST")
    if request.user.is_authenticated:
        return error("already_signed_in", 409)
    if request.content_type != "application/json":
        return error("invalid_request", 400)
    body = request.read(4097)
    if len(body) > 4096:
        return error("request_too_large", 413)
    try:
        value = json.loads(body, object_pairs_hook=_unique_object)
        if not isinstance(value, dict) or set(value) != {"username", "password"}:
            raise ValueError("Login fields")
        username, password = value["username"], value["password"]
        if (not isinstance(username, str) or not isinstance(password, str) or
                not 1 <= len(username) <= 150 or not 1 <= len(password) <= 1024 or
                "\0" in username or "\0" in password):
            raise ValueError("Login budget")
        username = AbstractBaseUser.normalize_username(username)
        if len(username) > 150:
            raise ValueError("Username budget")
    except (ValueError, UnicodeError, RecursionError):
        return error("invalid_request", 400)
    fingerprint = _claim_budget(username)
    if fingerprint is None:
        response = error("login_rate_limited", 429)
        response["Retry-After"] = "900"
        return response
    user = authenticate(request, username=username, password=password)
    if user is None or membership(user) is None:
        return error("invalid_credentials", 401)
    login(request, user)
    LoginBudget.objects.filter(fingerprint=fingerprint).delete()
    return JsonResponse({"user": {"id": str(user.pk)}, "csrf_token": get_token(request)})


def workspace_state(request):
    member, denied = authorize(request, "workspace.read")
    if denied is not None:
        return denied
    if request.method != "GET":
        return method_not_allowed("GET")
    return JsonResponse({"installation_id": str(member.installation_id), "role": member.role,
                         "permissions": list(PERMISSIONS[member.role])})
