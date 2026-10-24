"""Expose only the public identity of the frontend actually built on disk."""
import json
import re

from django.conf import settings
from django.http import JsonResponse

from .api import error, method_not_allowed


def version(request):
    if request.method != "GET":
        return method_not_allowed("GET")
    try:
        with (settings.FRONTEND_DIST / "build-info.json").open("rb") as file:
            raw = file.read(4097)
        if len(raw) > 4096:
            raise ValueError
        info = json.loads(raw.decode("utf-8"))
        if not isinstance(info, dict) or type(info.get("schema")) is not int or info["schema"] != 1:
            raise ValueError
        build_id = info.get("build_id")
        if not isinstance(build_id, str) or re.fullmatch(r"[0-9a-f]{64}", build_id) is None:
            raise ValueError
    except (OSError, UnicodeError, ValueError):
        return error("version_unavailable", 503)
    response = JsonResponse({"build_id": build_id})
    response["Cache-Control"] = "no-store"
    return response
