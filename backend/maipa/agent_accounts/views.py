"""Authenticated account metadata and zero-provider transport check history."""
from datetime import timedelta
import json

from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone

from backend.maipa.api import error, method_not_allowed
from .models import AgentAccount, TransportCheck
from . import runtime


def _authenticated(request):
    return request.user.is_authenticated and request.user.is_active


def accounts(request):
    if not _authenticated(request):
        return error("authentication_required", 401)
    if request.method != "GET":
        return method_not_allowed("GET")
    try:
        installed = runtime.installed_runtime()
    except runtime.TransportUnavailable:
        return error("agent_runtime_unavailable", 503)
    rows = AgentAccount.objects.filter(owner=request.user, enabled=True).order_by("provider", "id")
    return JsonResponse({"runtime": installed, "accounts": [
        {"id": str(row.pk), "provider": row.provider, "label": row.label, "status": "setup_required"}
        for row in rows
    ]})


def transport_checks(request, account_id):
    if not _authenticated(request):
        return error("authentication_required", 401)
    if request.method not in {"GET", "POST"}:
        return method_not_allowed("GET, POST")
    account = AgentAccount.objects.filter(pk=account_id, owner=request.user, enabled=True).first()
    if account is None:
        return error("not_found", 404)
    if request.method == "GET":
        rows = account.transport_checks.filter(requested_by=request.user).order_by("-created_at", "-id")[:20]
        return JsonResponse({"checks": [{"id": str(row.pk), "status": row.status, "provider_ready": False,
            "created_at": row.created_at, "completed_at": row.completed_at} for row in rows]})
    if request.content_type != "application/json":
        return error("invalid_request", 400)
    body = request.read(1025)
    if len(body) > 1024:
        return error("request_too_large", 413)
    try:
        value = json.loads(body)
        if not isinstance(value, dict) or value:
            raise ValueError("Only an empty request is accepted")
    except (ValueError, UnicodeError, RecursionError):
        return error("invalid_request", 400)
    now = timezone.now()
    eligible = Q(last_check_started_at__isnull=True) | Q(last_check_started_at__lte=now - timedelta(seconds=30))
    claimed = AgentAccount.objects.filter(pk=account.pk, owner=request.user, enabled=True).filter(eligible).update(last_check_started_at=now)
    if not claimed:
        response = error("check_rate_limited", 429)
        response["Retry-After"] = "30"
        return response
    record = TransportCheck.objects.create(account=account, provider=account.provider, requested_by=request.user)
    try:
        runtime.probe_transport(account)
    except runtime.TransportUnavailable:
        record.status = "unavailable"
    else:
        record.status = "transport_verified"
    record.completed_at = timezone.now()
    record.save(update_fields=["status", "completed_at"])
    if record.status != "transport_verified":
        return error("agent_transport_unavailable", 503)
    return JsonResponse({"id": str(record.pk), "status": record.status, "provider_ready": False}, status=201)
