"""Human session controls for agent-requested changes. There is no agent route to decide."""
from functools import wraps

from django.db import DatabaseError
from django.http import JsonResponse

from backend.maipa.api import error, method_not_allowed
from backend.maipa.intake.views import STATEMENT_BODY_BYTES, body
from backend.maipa.workspace.policy import authorize
from . import services


def endpoint(function):
    @wraps(function)
    def call(request, *args, **kwargs):
        _, denied = authorize(request, 'workspace.read')
        if denied is not None:
            return denied
        if request.headers.get('X-Maipa-Agent-Actor') != str(request.user.pk):
            return error('approval_actor_changed', 409)
        try:
            return function(request, *args, **kwargs)
        except services.ApprovalError as problem:
            code = problem.code
            return error(code, 404 if code == 'approval_not_found' else 403 if code == 'approval_denied'
                         else 409 if code.endswith('conflict') or code.endswith('decided') else 400)
        except DatabaseError:
            return error('approval_unavailable', 503)
    return call


@endpoint
def attachments(request):
    from . import attachments as files
    if request.method != 'POST':
        return method_not_allowed('POST')
    value, denied = body(request, STATEMENT_BODY_BYTES)
    if denied is not None:
        return denied
    try:
        return JsonResponse(files.store(request.user, value), status=201)
    except files.AttachmentError as problem:
        return error(problem.code, 403 if problem.code == 'attachment_denied' else 400)


@endpoint
def requests(request):
    if request.method != 'GET':
        return method_not_allowed('GET')
    return JsonResponse(services.list_requests(request.user))


@endpoint
def decide(request, request_id):
    if request.method != 'POST':
        return method_not_allowed('POST')
    value, denied = body(request)
    if denied is not None:
        return denied
    return JsonResponse(services.decide(request.user, str(request_id), value))


@endpoint
def decide_batch(request, batch_id):
    if request.method != 'POST':
        return method_not_allowed('POST')
    value, denied = body(request)
    if denied is not None:
        return denied
    return JsonResponse(services.decide_batch(request.user, str(batch_id), value))
