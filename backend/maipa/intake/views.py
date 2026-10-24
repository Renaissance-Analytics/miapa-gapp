"""Strict private intake API. Evidence references are inert text, never fetched."""
from functools import wraps
import json

from django.db import OperationalError
from django.http import JsonResponse
from django.utils import timezone

from backend.maipa.api import error, method_not_allowed
from backend.maipa.workspace.policy import authorize
from . import services
from .models import FactRevision
from .schema import InvalidFact, KEYS, day, require


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
        except services.ScopeDenied as exc:
            return error(exc.code, 403)
        except services.NotFound:
            return error('not_found', 404)
        except services.RevisionConflict:
            return error('revision_conflict', 409)
        except OperationalError:
            return error('intake_unavailable', 503)
    return dispatch


def body(request):
    require(request.content_type == 'application/json')
    raw = request.read(65537)
    if len(raw) > 65536:
        return None, error('request_too_large', 413)
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result)
            result[key] = value
        return result
    def constant(_):
        raise InvalidFact('Invalid JSON')
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
        require(isinstance(value, dict))
        return value, None
    except (ValueError, UnicodeError, RecursionError):
        raise InvalidFact('Invalid JSON') from None


def entity_summary(row):
    return {'id': str(row.pk), 'name': row.name, 'revision': row.revision}


def engagement_summary(row):
    return {'id': str(row.pk), 'name': row.name,
            'entities': [entity_summary(entity) for entity in row.entities.order_by('created_at', 'id')]}


def fact(row):
    return {'id': str(row.pk), 'revision_id': str(row.pk), 'key': row.key, 'revision': row.revision,
        'knowledge': row.knowledge, 'value': row.value, 'effective_from': row.effective_from,
        'effective_to': row.effective_to, 'evidence': row.evidence,
        'recorded_by': str(row.recorded_by_id), 'recorded_at': row.recorded_at}


@endpoint
def engagements(request):
    if request.method == 'GET':
        rows = services.owned_engagements(request.user).prefetch_related('entities').order_by('created_at', 'id')
        return JsonResponse({'engagements': [engagement_summary(row) for row in rows]})
    if request.method != 'POST':
        return method_not_allowed('GET, POST')
    services.scope(request.user, True)
    value, denied = body(request)
    if denied is not None:
        return denied
    row = services.create_engagement(request.user, value)
    return JsonResponse(engagement_summary(row), status=201)


@endpoint
def entities(request, engagement_id):
    if request.method != 'POST':
        return method_not_allowed('POST')
    services.scope(request.user, True)
    value, denied = body(request)
    if denied is not None:
        return denied
    return JsonResponse(entity_summary(services.create_entity(request.user, engagement_id, value)), status=201)


@endpoint
def entity(request, entity_id):
    if request.method != 'GET':
        return method_not_allowed('GET')
    row = services.owned_entity(request.user, entity_id)
    on = day(request.GET['on']) if 'on' in request.GET else timezone.now().date()
    selected = services.applicable(row, on)
    facts = {key: fact(selected[key]) if key in selected else
             {'knowledge': 'unknown', 'value': None, 'revision_id': None} for key in KEYS}
    return JsonResponse({**entity_summary(row), 'engagement_id': str(row.engagement_id), 'on': on,
        'facts': facts, 'can_edit': services.scope(request.user).role in ('administrator', 'preparer'),
        'stale_conclusions': row.conclusions.filter(status='stale').count()})


@endpoint
def facts(request, entity_id):
    if request.method != 'POST':
        return method_not_allowed('POST')
    services.owned_entity(request.user, entity_id, True)
    value, denied = body(request)
    if denied is not None:
        return denied
    return JsonResponse(fact(services.record_fact(request.user, entity_id, value)), status=201)


@endpoint
def history(request, entity_id, key):
    if request.method != 'GET':
        return method_not_allowed('GET')
    row = services.owned_entity(request.user, entity_id)
    if key not in KEYS:
        raise services.NotFound()
    rows = FactRevision.objects.filter(entity=row, key=key).order_by('-revision')
    if 'before' in request.GET:
        raw = request.GET['before']
        require(raw.isascii() and raw.isdigit() and 0 < len(raw) <= 10)
        require(0 < int(raw) <= 2_000_000_000)
        rows = rows.filter(revision__lt=int(raw))
    page = list(rows[:51])
    return JsonResponse({'revisions': [fact(row) for row in page[:50]],
        'next_before': page[49].revision if len(page)>50 else None})
