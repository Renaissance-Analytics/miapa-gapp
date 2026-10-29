"""Bounded owned definitions; all live data and native authority stay elsewhere."""
import json
import uuid

from django.db import DatabaseError, transaction
from django.db.models import F
from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables

from backend.maipa.api import error, method_not_allowed
from backend.maipa.agent_accounts.ui import validate_schema
from .models import Membership, PersonalViews
from .policy import authorize
from .views import _unique_object

MAX_VIEWS, MAX_HISTORY, MAX_COLLECTION_BYTES = 8, 4, 24_000


class StoredViewsUnavailable(Exception):
    pass


class ViewsFailure(Exception):
    def __init__(self, code, status):
        self.code, self.status = code, status
        super().__init__('Saved views unavailable')


def current_member(actor, *, lock=False):
    """Only a trusted authenticated principal; re-query active status and role."""
    if not isinstance(actor, get_user_model()) or actor.pk is None or not actor.is_authenticated:
        raise ViewsFailure('authentication_required', 401)
    rows = Membership.objects.select_related('installation')
    if lock:
        rows = rows.select_for_update()
    member = rows.filter(user_id=actor.pk, user__is_active=True, enabled=True,
        installation__slot=1, role__in=('administrator', 'preparer')).first()
    if member is None:
        raise ViewsFailure('workspace_access_denied', 403)
    return member


def require(condition):
    if not condition:
        raise ValueError('Invalid view definition')


def fields(value, expected):
    require(type(value) is dict and set(value) == set(expected))


def revision(value):
    require(type(value) is int and 0 <= value < 1_999_999_999)


def title(value):
    require(type(value) is str and bool(value.strip()) and len(value) <= 80)
    require(not any(ord(char) < 32 or ord(char) == 127 for char in value))
    return value


def identity(value):
    require(type(value) is str and str(uuid.UUID(value)) == value)
    return value


def definition(value, role):
    fields(value, ('revision', 'title', 'schema'))
    revision(value['revision']);require(value['revision'] > 0)
    return {'revision': value['revision'], 'title': title(value['title']),
            'schema': validate_schema(value['schema'], role)}


def stored(value):
    """Reject corrupt stored structures; role is checked separately on exposure."""
    try:
        require(type(value) is list and len(value) <= MAX_VIEWS)
        seen, result = set(), []
        for item in value:
            fields(item, ('id', 'title', 'schema', 'revision', 'pinned', 'history'))
            key = identity(item['id']);require(key not in seen);seen.add(key)
            require(type(item['pinned']) is bool)
            current = definition({key: item[key] for key in ('revision', 'title', 'schema')}, 'administrator')
            require(type(item['history']) is list and len(item['history']) <= MAX_HISTORY)
            history = [definition(entry, 'administrator') for entry in item['history']]
            numbers = [entry['revision'] for entry in history]
            require(numbers == sorted(set(numbers)) and all(n < current['revision'] for n in numbers))
            result.append({'id': key, **current, 'pinned': item['pinned'], 'history': history})
        return result
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise StoredViewsUnavailable from None


def visible(value, role):
    try:
        schema = validate_schema(value['schema'], role)
    except ValueError:
        schema = None
    return {**value, 'schema': schema, 'available': schema is not None}


def snapshot(row, role):
    output = []
    for view in stored(row.definitions):
        public = visible(view, role)
        public['history'] = [visible(entry, role) for entry in view['history']]
        output.append(public)
    return {'views_id': str(row.identity), 'user_id': str(row.user_id),
            'revision': row.revision, 'views': output}


def parse(request):
    require(request.content_type == 'application/json')
    body = request.read(65_537)
    if len(body) > 65_536:
        return None, error('request_too_large', 413)
    def invalid_constant(_):
        raise ValueError('Invalid JSON')
    value = json.loads(body, object_pairs_hook=_unique_object, parse_constant=invalid_constant)
    require(type(value) is dict)
    return value, None


def apply(previous, value, role):
    definitions = stored(previous)
    action = value.get('action')
    base = {'action', 'expected_revision'}
    if action == 'create':
        fields(value, base | {'title', 'schema'} | ({'id'} if 'id' in value else set()))
        schema = validate_schema(value['schema'], role);name = title(value['title'])
        key = identity(value['id']) if 'id' in value else str(uuid.uuid4())
        if any(view['id'] == key for view in definitions):
            raise ViewsFailure('revision_conflict', 409)
        if len(definitions) >= MAX_VIEWS:
            raise ViewsFailure('view_limit', 409)
        definitions.append({'id': key, 'title': name, 'schema': schema,
                            'revision': 1, 'pinned': True, 'history': []})
    elif action == 'reorder':
        fields(value, base | {'ids'})
        keys = value['ids'];require(type(keys) is list and len(keys) == len(definitions))
        require(all(type(key) is str for key in keys) and len(set(keys)) == len(keys))
        require(set(keys) == {view['id'] for view in definitions})
        by_id = {view['id']: view for view in definitions};definitions = [by_id[key] for key in keys]
    else:
        extras = {'rename': {'id', 'title'}, 'update': {'id', 'title', 'schema'},
                  'pin': {'id'}, 'unpin': {'id'}}
        require(type(action) is str and action in extras)
        fields(value, base | extras[action]);key = identity(value['id'])
        view = next((item for item in definitions if item['id'] == key), None)
        if view is None:
            raise ViewsFailure('view_unavailable', 404)
        before = {key: view[key] for key in ('revision', 'title', 'schema')}
        revision(view['revision'])
        if action in ('rename', 'update'):
            view['title'] = title(value['title'])
        if action == 'update':
            view['schema'] = validate_schema(value['schema'], role)
        elif action in ('pin', 'unpin'):
            view['pinned'] = action == 'pin'
        view['history'] = (view['history'] + [before])[-MAX_HISTORY:]
        view['revision'] += 1
    if len(json.dumps(definitions, ensure_ascii=True, separators=(',', ':')).encode('utf-8')) > MAX_COLLECTION_BYTES:
        raise ViewsFailure('view_limit', 409)
    return definitions


@sensitive_variables('row')
def read_personal_views(actor):
    with transaction.atomic():
        member = current_member(actor, lock=True)
        row, _ = PersonalViews.objects.get_or_create(user=actor, defaults={'installation': member.installation})
        if row.installation_id != member.installation_id:
            raise ViewsFailure('workspace_access_denied', 403)
        return snapshot(row, member.role)


@sensitive_variables('value', 'row', 'definitions')
def mutate_personal_views(actor, expected_identity, value):
    require(type(value) is dict)
    revision(value.get('expected_revision'))
    try:
        expected_identity = identity(expected_identity)
    except (ValueError, TypeError, AttributeError):
        raise ViewsFailure('revision_conflict', 409) from None
    with transaction.atomic():
        member = current_member(actor, lock=True)
        # This CAS acquires SQLite's write lock before definitions are read.
        rows = PersonalViews.objects.filter(user=actor, installation=member.installation,
            identity=expected_identity, revision=value['expected_revision'])
        if rows.update(revision=F('revision') + 1) != 1:
            raise ViewsFailure('revision_conflict', 409)
        row = PersonalViews.objects.get(user=actor, installation=member.installation)
        row.definitions = apply(row.definitions, value, member.role)
        row.save(update_fields=('definitions',))
        return snapshot(row, member.role)


def owned_definition(actor, views_id, expected_revision, key):
    revision(expected_revision);identity(key)
    state = read_personal_views(actor)
    if state['views_id'] != views_id or state['revision'] != expected_revision:
        raise ViewsFailure('revision_conflict', 409)
    view = next((item for item in state['views'] if item['id'] == key and item['available']), None)
    if view is None:
        raise ViewsFailure('view_unavailable', 404)
    return {'title': view['title'], 'schema': view['schema']}


@sensitive_post_parameters()
@sensitive_variables('value', 'row', 'definitions')
def personal_views(request):
    member, denied = authorize(request, 'agent.transport.check')
    if denied is not None:
        return denied
    if request.method not in ('GET', 'POST'):
        return method_not_allowed('GET, POST')
    if request.GET:
        return error('invalid_request', 400)
    try:
        if request.method == 'GET':
            return JsonResponse(read_personal_views(request.user))
        value, denied = parse(request)
        if denied is not None:
            return denied
        return JsonResponse(mutate_personal_views(request.user, request.headers.get('X-Maipa-Views'), value))
    except ViewsFailure as failure:
        return error(failure.code, failure.status)
    except StoredViewsUnavailable:
        return error('views_unavailable', 503)
    except DatabaseError:
        return error('views_unavailable', 503)
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        return error('invalid_request', 400)
