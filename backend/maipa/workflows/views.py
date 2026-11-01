"""Human session endpoints only; never registered in the native/model tool set."""
from functools import wraps
import json
from django.db import OperationalError
from django.http import JsonResponse
from backend.maipa.api import error,method_not_allowed
from backend.maipa.intake.views import body
from backend.maipa.intake.schema import InvalidFact
from backend.maipa.workspace.policy import authorize
from . import gates,drafts
from backend.maipa.acp import _object,_invalid_number
from .errors import Unavailable,InvalidWorkflow,Conflict
from .schema import require


def endpoint(function):
    @wraps(function)
    def call(request,*args,**kwargs):
        _,denied=authorize(request,'workspace.read')
        if denied is not None:return denied
        try:return function(request,*args,**kwargs)
        except (InvalidWorkflow,InvalidFact):return error('invalid_request',400)
        except Unavailable:return error('not_found',404)
        except Conflict:return error('revision_conflict',409)
        except OperationalError:return error('workflow_unavailable',503)
    return call


@endpoint
def pending(request,run_id):
    if request.method!='GET':return method_not_allowed('GET')
    value=gates.get_pending(request.user,str(run_id))
    return JsonResponse(value,safe=value is not None)


@endpoint
def response(request,run_id,gate_id):
    if request.method!='POST':return method_not_allowed('POST')
    command,denied=body(request)
    if denied is not None:return denied
    require(set(command)=={'expected_step','definition_revision','request_sha256','request_key','answer'})
    return JsonResponse(gates.respond(request.user,str(run_id),str(gate_id),**command))


def _draft_actor(request):
    # A consistency guard for an already authenticated human UI; never authority
    # selected by a header. Native callers use the separate sealed service actor.
    if request.headers.get('X-Maipa-Workflow-Actor')!=str(request.user.pk):return error('actor_changed',409)
    if request.GET:return error('invalid_request',400)


def _draft_body(request,fields):
    require(request.content_type=='application/json')
    raw=request.read(65537)
    if len(raw)>65536:return None,error('request_too_large',413)
    try:value=json.loads(raw,object_pairs_hook=_object,parse_constant=_invalid_number)
    except (ValueError,UnicodeError,RecursionError):
        return None,error('invalid_request',400)
    require(type(value) is dict and set(value)==set(fields))
    return value,None


@endpoint
def authoring(request):
    if request.method not in ('GET','POST'):return method_not_allowed('GET, POST')
    denied=_draft_actor(request)
    if denied is not None:return denied
    if request.method=='GET':return JsonResponse({'drafts':drafts.list_drafts(request.user)})
    command,denied=_draft_body(request,('name','entity_id','request_key'))
    if denied is not None:return denied
    return JsonResponse(drafts.create(request.user,command['name'],command['request_key'],entity_id=command['entity_id']),status=201)


@endpoint
def draft(request,draft_id):
    if request.method not in ('GET','POST'):return method_not_allowed('GET, POST')
    denied=_draft_actor(request)
    if denied is not None:return denied
    if request.method=='GET':return JsonResponse(drafts.get(request.user,str(draft_id)))
    command,denied=_draft_body(request,('expected_revision','graph'))
    if denied is not None:return denied
    return JsonResponse(drafts.change(request.user,str(draft_id),**command))


@endpoint
def publish_draft(request,draft_id):
    if request.method!='POST':return method_not_allowed('POST')
    denied=_draft_actor(request)
    if denied is not None:return denied
    command,denied=_draft_body(request,('expected_revision',))
    if denied is not None:return denied
    return JsonResponse(drafts.publish(request.user,str(draft_id),**command))
