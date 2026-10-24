"""Minimal session/liveness transport. Finance and provider authority is separate."""
from django.contrib.auth import logout
from django.http import JsonResponse
from django.middleware.csrf import get_token


def error(code, status):
    return JsonResponse({"error": {"code": code}}, status=status)


def method_not_allowed(allowed):
    response = error("method_not_allowed", 405)
    response["Allow"] = allowed
    return response


def health(request):
    if request.method != "GET":
        return method_not_allowed("GET")
    return JsonResponse({"status": "ok"})


def session_state(request):
    if request.method != "GET":
        return method_not_allowed("GET")
    if not request.user.is_authenticated or not request.user.is_active:
        return error("authentication_required", 401)
    return JsonResponse({"user": {"id": str(request.user.pk)}, "csrf_token": get_token(request)})


def session_logout(request):
    if request.method != "POST":
        return method_not_allowed("POST")
    if not request.user.is_authenticated or not request.user.is_active:
        return error("authentication_required", 401)
    logout(request)
    return JsonResponse({"status": "signed_out"})


def csrf_failure(request, reason=""):
    return error("csrf_failed", 403)


def bad_request(request, exception=None):
    return error("bad_request", 400)


def forbidden(request, exception=None):
    return error("forbidden", 403)


def not_found(request, exception=None):
    return error("not_found", 404)


def server_error(request):
    return error("internal_error", 500)
