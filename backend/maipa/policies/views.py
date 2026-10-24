"""Bounded human lifecycle APIs. No root provisioning or actor-kind parameter."""
from functools import wraps

from django.db import OperationalError
from django.http import JsonResponse

from backend.maipa.api import error, method_not_allowed
from backend.maipa.intake import services
from backend.maipa.intake.schema import InvalidFact, require
from backend.maipa.intake.views import body
from backend.maipa.workspace.policy import authorize
from . import authority
from .models import AuthorityGrant, PolicyEpoch


def endpoint(function):
    @wraps(function)
    def dispatch(request, *args, **kwargs):
        _, denied = authorize(request, 'workspace.read')
        if denied is not None:
            return denied
        try:
            return function(request, *args, **kwargs)
        except InvalidFact:
            return error('invalid_request', 400)
        except (authority.Denied, services.ScopeDenied):
            return error('authority_denied', 403)
        except services.NotFound:
            return error('not_found', 404)
        except OperationalError:
            return error('policy_unavailable', 503)
    return dispatch


def grant_summary(row):
    return dict(id=str(row.pk), action=row.action, duty=row.duty, limit=row.limit, currency=row.currency,
        starts_at=row.starts_at, expires_at=row.expires_at, evidence=row.evidence,
        parent_id=str(row.parent_id) if row.parent_id else None, can_delegate=row.can_delegate,
        current=authority.grant_is_current(row.pk), revoked_at=row.revoked_at,
        actor_kind='agent' if row.agent_id else 'human')


@endpoint
def entity_authority(request, entity_id):
    if request.method != 'GET':
        return method_not_allowed('GET')
    entity = services.owned_entity(request.user, entity_id)
    before = request.GET.get('before')
    rows = AuthorityGrant.objects.filter(entity=entity, subject=request.user).order_by('-created_at', '-id')
    # Stable UUID cursor: resolve the persisted boundary inside the same private scope.
    if before is not None:
        import uuid
        from django.db.models import Q
        try:
            identity = uuid.UUID(before)
        except ValueError:
            raise InvalidFact('Invalid cursor') from None
        boundary = rows.filter(pk=identity).first()
        require(boundary is not None)
        rows = rows.filter(Q(created_at__lt=boundary.created_at) | Q(created_at=boundary.created_at, id__lt=boundary.pk))
    page = list(rows[:51])
    version = PolicyEpoch.objects.filter(entity=entity).values_list('version', flat=True).first() or 0
    return JsonResponse(dict(entity_id=str(entity.pk), policy_version=version, financial_execution_available=False,
        grants=[grant_summary(row) for row in page[:50]], next_before=str(page[49].pk) if len(page)>50 else None))


@endpoint
def revoke_grant(request, grant_id):
    if request.method != 'POST':
        return method_not_allowed('POST')
    value, denied = body(request)
    if denied is not None:
        return denied
    require(set(value) == {'reason'})
    authority.revoke(authority.human(request.user), grant_id, value['reason'])
    return JsonResponse({'status': 'revoked'})


@endpoint
def delegate_grant(request, grant_id):
    if request.method != 'POST':
        return method_not_allowed('POST')
    value, denied = body(request)
    if denied is not None:
        return denied
    require(set(value) == {'subject_id', 'starts_at', 'expires_at', 'limit', 'currency', 'evidence'})
    raw_subject = value['subject_id']
    require(type(raw_subject) is str and raw_subject.isascii() and raw_subject.isdigit() and len(raw_subject) <= 20)
    row = authority.delegate(authority.human(request.user), grant_id, subject_id=int(raw_subject),
        starts_at=authority._date(value['starts_at']), expires_at=authority._date(value['expires_at']),
        limit=value['limit'], currency=value['currency'], evidence=value['evidence'])
    return JsonResponse(grant_summary(row), status=201)
