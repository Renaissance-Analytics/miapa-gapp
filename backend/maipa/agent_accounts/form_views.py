"""Authenticated human adapter over exactly the native agent's pending form service."""
import json
from django.http import JsonResponse
from backend.maipa.acp import _object, _invalid_number
from backend.maipa.api import error,method_not_allowed
from backend.maipa.workspace.policy import authorize
from .form_drafts import read_new_client_form,change_new_client_form,FormConflict,FormUnavailable


def form(request,account_id,identity,component_id):
    _,denied=authorize(request,'agent.transport.check')
    if denied is not None:return denied
    if request.method not in ('GET','POST'):return method_not_allowed('GET, POST')
    if request.headers.get('X-Maipa-Agent-Actor')!=str(request.user.pk):return error('actor_changed',409)
    if request.GET:return error('invalid_request',400)
    try:
        if request.method=='GET':return JsonResponse(read_new_client_form(request.user,str(account_id),str(identity),component_id))
        if request.content_type!='application/json':return error('invalid_request',400)
        raw=request.read(65_537)
        if len(raw)>65_536:return error('request_too_large',413)
        value=json.loads(raw,object_pairs_hook=_object,parse_constant=_invalid_number)
        if type(value) is not dict or set(value)!={'id','expected_revision','values'} or type(value['expected_revision']) is not int:
            return error('invalid_request',400)
        return JsonResponse(change_new_client_form(request.user,str(account_id),str(identity),component_id,value))
    except FormConflict:return error('draft_changed',409)
    except FormUnavailable:return error('form_unavailable',404)
    except (ValueError,UnicodeError,RecursionError):return error('invalid_request',400)
