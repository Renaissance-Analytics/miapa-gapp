"""Serve a local built UI beside the API; never a production asset host."""
from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation
from django.http import FileResponse
from django.views.static import serve

from .api import error, method_not_allowed


def shell(request):
    if not settings.LOCAL_DEV:
        return error("not_found", 404)
    if request.method != "GET":
        return method_not_allowed("GET")
    path = settings.FRONTEND_DIST / "index.html"
    try:
        response = FileResponse(path.open("rb"), content_type="text/html; charset=utf-8")
    except FileNotFoundError:
        return error("frontend_unavailable", 503)
    response["Cache-Control"] = "no-store"
    return response


def asset(request, path):
    if not settings.LOCAL_DEV:
        return error("not_found", 404)
    if request.method != "GET":
        return method_not_allowed("GET")
    # Django's published safe-join/static handler refuses traversal outside
    # the trusted built asset directory. Runtime/env files are never under it.
    try:
        return serve(request, path, document_root=settings.FRONTEND_DIST / "assets")
    except SuspiciousFileOperation:
        return error("not_found", 404)
