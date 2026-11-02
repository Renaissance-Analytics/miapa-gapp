import json
from django.db import DatabaseError
from django.http import JsonResponse
from backend.maipa.acp import _object,_invalid_number
from backend.maipa.api import error,method_not_allowed
from .chat_views import _guard
from . import draft_store

def drafts(request,account_id=None):
    denied=_guard(request)
    if denied is not None:return denied
    account_id=str(account_id) if account_id is not None else None
    try:
        if request.method=='GET':return JsonResponse(draft_store.read(request.user,account_id))
        if request.method!='POST':return method_not_allowed('GET, POST')
        if request.content_type!='application/json':raise ValueError()
        raw=request.read(196609)
        if len(raw)>196608:return error('request_too_large',413)
        value=json.loads(raw,object_pairs_hook=_object,parse_constant=_invalid_number)
        if type(value) is not dict or set(value)!={'expected_revision','draft','pending'}:raise ValueError()
        return JsonResponse(draft_store.save(request.user,account_id,**value))
    except draft_store.DraftConflict:return error('draft_changed',409)
    except draft_store.DraftUnavailable:return error('draft_unavailable',404)
    except DatabaseError:return error('draft_unavailable',503)
    except (ValueError,TypeError,UnicodeError,RecursionError):return error('invalid_request',400)
