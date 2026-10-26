"""Application-owned UI tools. Models supply schemas, never actor/path/code authority."""
from functools import wraps
import json
import re

from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.db.models import F

from backend.maipa.workspace.models import Membership
from .models import SessionReference, UiComposition
from .references import _session, _uuid

MESSAGE = 'UI composition unavailable'
HANDLE = re.compile(r'[a-z][a-z0-9-]{0,47}', re.ASCII)
CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


class UiUnavailable(Exception):
    """A fixed error with no private binding or model data."""


class UiConflict(UiUnavailable):
    """The current controlled state must be read again."""


def _safe(operation):
    @wraps(operation)
    def call(*args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except (OSError, ValueError, TypeError, AttributeError, OverflowError,
                RecursionError, DatabaseError, ObjectDoesNotExist):
            raise UiUnavailable(MESSAGE) from None
    return call


def _object(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        raise ValueError('Closed object required')


def _member(actor, saved):
    return Membership.objects.select_related('installation').get(
        user_id=actor.pk, enabled=True, installation_id=saved.installation_id,
        role__in=('administrator', 'preparer'))


def _lookup(actor, account_id, identity):
    saved = UiComposition.objects.get(pk=_uuid(identity), session__user_id=actor.pk,
                                      session__account_id=_uuid(account_id))
    original = _session(actor, account_id, str(saved.session_id))
    member = _member(actor, original)
    if member.role != saved.role or member.installation.access_revision != saved.access_revision:
        raise ValueError('Original UI authority changed')
    return saved


def _components(role):
    items = [
        ('Maipa.Stack', 'A vertical group of one to sixteen registered components.'),
        ('Maipa.Note', 'Unverified agent commentary; props contains only text, up to 2000 characters.'),
        ('Maipa.ClientFacts', 'Owned client facts and evidence; no props. Server-scoped services load the data.'),
        ('Maipa.ClientCatalog', 'Owned client/entity metadata controls; no props. Writes require current service authority.'),
    ]
    if role == 'administrator':
        items.append(('Maipa.AdminUsers', 'Local user/access controls; no props. Every operation rechecks server administrator authority.'))
    return [{'type': kind, 'description': description} for kind, description in items]


def validate_schema(value, role):
    # Detach even accepted values and reject non-JSON/cyclic/excessive payloads.
    wire = json.dumps(value, ensure_ascii=True, allow_nan=False)
    if len(wire.encode('utf-8')) > 32_768:
        raise ValueError('Schema budget')
    value = json.loads(wire)
    allowed = {item['type'] for item in _components(role)}
    identities, controls = set(), set()
    count = 0

    def node(item, depth):
        nonlocal count
        count += 1
        if (type(item) is not dict or not {'id', 'type'}.issubset(item) or
                set(item) - {'id', 'type', 'props', 'children'} or
                type(item['id']) is not str or not HANDLE.fullmatch(item['id']) or
                item['id'] in identities or type(item['type']) is not str or
                item['type'] not in allowed or depth > 4 or count > 16):
            raise ValueError('Invalid component')
        identities.add(item['id'])
        if item['type'] == 'Maipa.Stack':
            _object(item, ('id', 'type', 'children'))
            if type(item['children']) is not list or not 1 <= len(item['children']) <= 16:
                raise ValueError('Invalid group')
            for child in item['children']:
                node(child, depth + 1)
        elif item['type'] == 'Maipa.Note':
            _object(item, ('id', 'type', 'props'))
            _object(item['props'], ('text',))
            text = item['props']['text']
            if type(text) is not str or not text.strip() or len(text) > 2000 or CONTROL.search(text):
                raise ValueError('Invalid note')
        else:
            _object(item, ('id', 'type'))
            if item['type'] in controls:
                raise ValueError('Repeated interactive component')
            controls.add(item['type'])
    node(value, 0)
    return value


def _composition(value, role):
    _object(value, ('title', 'schema'))
    title = value['title']
    if (type(title) is not str or not title.strip() or len(title) > 80 or
            any(ord(char) < 32 or ord(char) == 127 for char in title)):
        raise ValueError('Invalid title')
    return {'title': title, 'schema': validate_schema(value['schema'], role)}


def _snapshot(saved):
    return {'id': str(saved.pk), 'revision': saved.revision,
            'composition': None if saved.composition is None else _composition(saved.composition, saved.role)}


@_safe
def open_ui_lease(actor, account_id, session_id):
    """Trusted session consumer only. No HTTP/model operation mints a lease."""
    with transaction.atomic():
        SessionReference.objects.filter(pk=_uuid(session_id), user_id=actor.pk,
                                        account_id=_uuid(account_id)).update(handle_digest=F('handle_digest'))
        original = _session(actor, account_id, session_id)
        member = _member(actor, original)
        if UiComposition.objects.filter(session_id=original.pk).count() >= 8:
            raise ValueError('UI lease budget')
        saved = UiComposition.objects.create(session=original, role=member.role,
                                             access_revision=member.installation.access_revision)
        _lookup(actor, account_id, str(saved.pk))
        return str(saved.pk)


@_safe
def ui_snapshot(actor, account_id, identity):
    return _snapshot(_lookup(actor, account_id, identity))


def _tools():
    revision = {'type': 'integer', 'minimum': 0, 'maximum': 1_999_999_998}
    definitions = []
    for name, description, properties in (
        ('maipa_ui_catalog', 'Read the current revision and supported component catalog before presenting UI.', {}),
        ('maipa_ui_present', 'Present contextual UI when none is open. Use stable unique node IDs; never executable code or data selectors.',
         {'revision': revision, 'title': {'type': 'string', 'minLength': 1, 'maxLength': 80}, 'schema': {'type': 'object'}}),
        ('maipa_ui_update', 'Replace the current composition at its exact current revision, preserving chat.',
         {'revision': revision, 'title': {'type': 'string', 'minLength': 1, 'maxLength': 80}, 'schema': {'type': 'object'}}),
        ('maipa_ui_dismiss', 'Dismiss contextual UI at its current revision and give space back to chat.', {'revision': revision}),
    ):
        definitions.append({'name': name, 'description': description, 'inputSchema': {
            'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}})
    return definitions


@_safe
def dispatch_ui(actor, account_id, identity, request):
    """Untrusted request, trusted actor/account/lease supplied independently."""
    _object(request, ('method', 'params'))
    method, params = request['method'], request['params']
    if method == 'tools/list':
        _object(params, ())
        _lookup(actor, account_id, identity)
        return {'tools': _tools()}
    if method != 'tools/call':
        raise ValueError('Unsupported UI method')
    _object(params, ('name', 'arguments'))
    name, arguments = params['name'], params['arguments']
    if name == 'maipa_ui_catalog':
        _object(arguments, ())
        saved = _lookup(actor, account_id, identity)
        return {'revision': saved.revision, 'components': _components(saved.role),
                'composition': _snapshot(saved)['composition'],
                'schema_contract': {
                    'node_fields': ['id', 'type'], 'id_pattern': HANDLE.pattern,
                    'stack': 'Maipa.Stack requires children; no props. Other types cannot have children.',
                    'note': 'Maipa.Note requires props containing only text. All other leaf components have no props.',
                    'example': {'id': 'review-note', 'type': 'Maipa.Note', 'props': {'text': 'Commentary to verify against evidence.'}},
                },
                'limits': {'nodes': 16, 'depth': 4, 'schema_bytes': 32_768}}
    if name not in ('maipa_ui_present', 'maipa_ui_update', 'maipa_ui_dismiss'):
        raise ValueError('Unsupported UI tool')
    _object(arguments, ('revision',) if name == 'maipa_ui_dismiss' else ('revision', 'title', 'schema'))
    revision = arguments['revision']
    if type(revision) is not int or not 0 <= revision < 1_999_999_999:
        raise ValueError('Invalid revision')
    with transaction.atomic():
        # Acquire the actual write lock first, avoiding SQLite read-upgrade
        # races. Authority and CAS are still checked inside this transaction.
        UiComposition.objects.filter(pk=_uuid(identity), session__user_id=actor.pk,
                                     session__account_id=_uuid(account_id)).update(revision=F('revision'))
        saved = _lookup(actor, account_id, identity)
        if saved.revision != revision:
            raise UiConflict(MESSAGE)
        if ((name == 'maipa_ui_present' and saved.composition is not None) or
                (name != 'maipa_ui_present' and saved.composition is None)):
            raise ValueError('Invalid presentation lifecycle')
        composition = None if name == 'maipa_ui_dismiss' else _composition(
            {'title': arguments['title'], 'schema': arguments['schema']}, saved.role)
        if UiComposition.objects.filter(pk=saved.pk, revision=revision).update(
                revision=revision + 1, composition=composition) != 1:
            raise UiConflict(MESSAGE)
        # Check the original authority again before returning accepted content.
        return _snapshot(_lookup(actor, account_id, identity))


def bind_ui_handler(actor, account_id, identity):
    """An ACP callback closure; RPC arguments cannot change the original binding."""
    ui_snapshot(actor, account_id, identity)
    user_id, account_id, identity = actor.pk, _uuid(account_id), _uuid(identity)

    @sync_to_async(thread_sensitive=True)
    def dispatch(request):
        return dispatch_ui(get_user_model().objects.get(pk=user_id), account_id, identity, request)

    async def handle(request):
        return await dispatch(request)
    return handle
