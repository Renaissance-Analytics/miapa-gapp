"""Shared authenticated UI state. No public session/lease creation or arbitrary RPC."""
import json

from django.http import JsonResponse

from backend.maipa.acp import _object, _invalid_number
from backend.maipa.api import error, method_not_allowed
from backend.maipa.workspace.policy import authorize
from .ui import UiConflict, UiUnavailable, dispatch_ui, ui_snapshot


def composition(request, account_id, identity):
    _, denied = authorize(request, 'agent.transport.check')
    if denied is not None:
        return denied
    if request.method not in ('GET', 'POST'):
        return method_not_allowed('GET, POST')
    if request.headers.get('X-Maipa-Agent-Actor') != str(request.user.pk):
        return error('actor_changed', 409)
    if request.GET:
        return error('invalid_request', 400)
    try:
        if request.method == 'GET':
            return JsonResponse(ui_snapshot(request.user, str(account_id), str(identity)))
        if request.content_type != 'application/json':
            return error('invalid_request', 400)
        body = request.read(65_537)
        if len(body) > 65_536:
            return error('request_too_large', 413)
        value = json.loads(body, object_pairs_hook=_object, parse_constant=_invalid_number)
        return JsonResponse(dispatch_ui(request.user, str(account_id), str(identity), value))
    except UiConflict:
        return error('revision_conflict', 409)
    except UiUnavailable:
        return error('ui_composition_unavailable', 404)
    except (ValueError, UnicodeError, RecursionError):
        return error('invalid_request', 400)
