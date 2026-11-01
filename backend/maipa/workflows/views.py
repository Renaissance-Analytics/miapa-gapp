"""Human session endpoints only; never registered in the native/model tool set."""
from functools import wraps
from django.db import OperationalError
from django.http import JsonResponse
from backend.maipa.api import error,method_not_allowed
from backend.maipa.intake.views import body
from backend.maipa.intake.schema import InvalidFact
from backend.maipa.workspace.policy import authorize
from . import gates
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
