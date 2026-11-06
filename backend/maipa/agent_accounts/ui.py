"""Application-owned UI tools. Models supply schemas, never actor/path/code authority."""
from functools import wraps
from uuid import UUID
import json
import re

from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.db import connections
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
        ('Maipa.Heading', 'Compact section heading; props contains only text, up to 120 characters.'),
        ('Maipa.Callout', 'Unverified commentary in a Fancy callout; props: text up to 2000 characters, tone info/attention/neutral.'),
        ('Maipa.ClientTable', 'Read-only current owned clients from the scoped service, not supplied rows. Props: columns, an ordered unique subset of name/entity_count. No identifiers, bindings or data snapshots.'),
        ('Maipa.ClientFacts', 'Owned client facts and evidence; no props. Server-scoped services load the data.'),
        ('Maipa.ClientCatalog', 'Owned client/entity metadata controls; no props. Writes require current service authority.'),
        ('Maipa.AgentSettings', 'Owned account/profile metadata and human controls for the actual provider connection/model; no props, secrets or model authorization. Model choices apply only to the live connection.'),
        ('Maipa.Workflow', 'Shared saved workflow editor. Props: draft_id, the UUID of an owned authoring draft. Loads current saved graph through authorized services; no graph, actor or authority props.'),
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
        elif item['type'] in ('Maipa.Note', 'Maipa.Heading', 'Maipa.Callout'):
            _object(item, ('id', 'type', 'props'))
            _object(item['props'], ('text', 'tone') if item['type'] == 'Maipa.Callout' else ('text',))
            text = item['props']['text']
            limit = 120 if item['type'] == 'Maipa.Heading' else 2000
            if type(text) is not str or not text.strip() or len(text) > limit or CONTROL.search(text):
                raise ValueError('Invalid note')
            if item['type'] == 'Maipa.Callout' and item['props']['tone'] not in ('info', 'attention', 'neutral'):
                raise ValueError('Invalid commentary tone')
        elif item['type'] == 'Maipa.Workflow':
            _object(item,('id','type','props'));_object(item['props'],('draft_id',))
            identity=item['props']['draft_id']
            if type(identity) is not str or str(UUID(identity))!=identity or item['type'] in controls:
                raise ValueError('Invalid workflow reference')
            controls.add(item['type'])
        elif item['type'] == 'Maipa.ClientTable':
            _object(item, ('id', 'type', 'props'))
            _object(item['props'], ('columns',))
            columns = item['props']['columns']
            if (type(columns) is not list or not 1 <= len(columns) <= 2 or
                    any(type(column) is not str or column not in ('name', 'entity_count') for column in columns) or
                    len(set(columns)) != len(columns) or item['type'] in controls):
                raise ValueError('Invalid table definition')
            controls.add(item['type'])
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
    view_identity = {'type': 'string', 'format': 'uuid'}
    for name, description, properties, required in (
        ('maipa_views_list', 'Read only your saved component definitions and their current collection revision; no old session authority.', {}, []),
        ('maipa_views_save', 'Save and pin a safe personal component definition. Wait for acknowledged persistence. Reusing a stale revision cannot create a duplicate.',
         {'views_id': view_identity, 'expected_revision': revision, 'title': {'type':'string','minLength':1,'maxLength':80},
          'schema': {'type':'object'}, 'id': view_identity}, ['views_id','expected_revision','title','schema']),
        ('maipa_views_change', 'Update, rename, reorder, pin or unpin your saved definitions at the current collection revision. Read current state first; unpin retains history.',
         {'views_id': view_identity, 'expected_revision': revision, 'action': {'type':'string','enum':['rename','update','pin','unpin','reorder']},
          'id': view_identity, 'title': {'type':'string','minLength':1,'maxLength':80}, 'schema': {'type':'object'},
          'ids': {'type':'array','items':view_identity,'maxItems':8}, 'revision': revision}, ['views_id','expected_revision','action']),
        ('maipa_views_open', 'Open a saved definition using current role and fresh UI authority. Supply both current collection and UI revisions; never opens historical authority.',
         {'views_id':view_identity,'expected_revision':revision,'id':view_identity,'revision':revision},
         ['views_id','expected_revision','id','revision']),
    ):
        definitions.append({'name': name, 'description': description, 'inputSchema': {
            'type':'object','properties':properties,'required':required,'additionalProperties':False}})
    return definitions


def _dispatch_views(actor, account_id, identity, name, arguments):
    # Import lazily: the definition service reuses this module's closed schema validator.
    from backend.maipa.workspace import personal_views as views
    try:
        with transaction.atomic():
            UiComposition.objects.filter(pk=_uuid(identity), session__user_id=actor.pk,
                session__account_id=_uuid(account_id)).update(revision=F('revision'))
            _lookup(actor, account_id, identity)
            if name == 'maipa_views_list':
                _object(arguments, ())
                return views.read_personal_views(actor)
            if name == 'maipa_views_open':
                _object(arguments, ('views_id','expected_revision','id','revision'))
                definition = views.owned_definition(actor, arguments['views_id'], arguments['expected_revision'], arguments['id'])
                saved = _lookup(actor, account_id, identity)
                operation = 'maipa_ui_update' if saved.composition else 'maipa_ui_present'
                return dispatch_ui(actor, account_id, identity, {'method':'tools/call','params':{
                    'name':operation,'arguments':{'revision':arguments['revision'], **definition}}})
            if name == 'maipa_views_save':
                _object(arguments, ('views_id','expected_revision','title','schema') + (('id',) if 'id' in arguments else ()))
                value = {'action':'create', **{key:value for key,value in arguments.items() if key != 'views_id'}}
            elif name == 'maipa_views_change':
                if type(arguments) is not dict or arguments.get('action') not in ('rename','update','pin','unpin','reorder'):
                    raise ValueError('Invalid view operation')
                value = {key:value for key,value in arguments.items() if key not in ('views_id','revision')}
            else:
                raise ValueError('Unsupported saved-view tool')
            result = views.mutate_personal_views(actor, arguments.get('views_id'), value)
            if 'revision' in arguments:
                if name != 'maipa_views_change' or arguments['action'] not in ('rename','update'):
                    raise ValueError('Invalid visible update')
                definition = views.owned_definition(actor, result['views_id'], result['revision'], arguments['id'])
                saved = _lookup(actor, account_id, identity)
                if saved.composition is None:
                    raise ValueError('No visible composition')
                accepted = dispatch_ui(actor, account_id, identity, {'method':'tools/call','params':{
                    'name':'maipa_ui_update','arguments':{'revision':arguments['revision'],**definition}}})
                return {'views':result,'ui':accepted}
            _lookup(actor, account_id, identity)
            return result
    except views.ViewsFailure as failure:
        if failure.status == 409:
            raise UiConflict(MESSAGE) from None
        raise UiUnavailable(MESSAGE) from None
    except views.StoredViewsUnavailable:
        raise UiUnavailable(MESSAGE) from None


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
    if name in ('maipa_views_list','maipa_views_save','maipa_views_change','maipa_views_open'):
        return _dispatch_views(actor, account_id, identity, name, arguments)
    if name == 'maipa_ui_catalog':
        _object(arguments, ())
        saved = _lookup(actor, account_id, identity)
        return {'revision': saved.revision, 'components': _components(saved.role),
                'composition': _snapshot(saved)['composition'],
                'schema_contract': {
                    'node_fields': ['id', 'type'], 'id_pattern': HANDLE.pattern,
                    'stack': 'Maipa.Stack requires children; no props. Other types cannot have children.',
                    'commentary': 'Maipa.Note and Maipa.Heading require props containing only text. Maipa.Callout requires text and tone (info/attention/neutral). Text is commentary, never verified records.',
                    'client_table': 'Maipa.ClientTable requires props containing only columns: one or both of name/entity_count, unique and ordered. Current owned data loads from the service; never supply rows, user/entity IDs, URLs or selectors. At most one table per composition.',
                    'services': 'Maipa.ClientFacts/ClientCatalog/AdminUsers have no props; their services enforce current authority.',
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
        from .form_drafts import retain_visible_drafts
        if UiComposition.objects.filter(pk=saved.pk, revision=revision).update(
                revision=revision + 1, composition=composition,
                form_drafts=retain_visible_drafts(saved.form_drafts, composition)) != 1:
            raise UiConflict(MESSAGE)
        # Check the original authority again before returning accepted content.
        return _snapshot(_lookup(actor, account_id, identity))


def bind_ui_handler(actor, account_id, identity):
    """An ACP callback closure; RPC arguments cannot change the original binding."""
    ui_snapshot(actor, account_id, identity)
    user_id, account_id, identity = actor.pk, _uuid(account_id), _uuid(identity)

    @sync_to_async(thread_sensitive=True)
    def dispatch(request):
        for connection in connections.all():
            if not connection.in_atomic_block:
                connection.close_if_unusable_or_obsolete()
        try:
            return dispatch_ui(get_user_model().objects.get(pk=user_id), account_id, identity, request)
        finally:
            # Background consumers have no Django request-finished hook. Keep an
            # enclosing test/request transaction intact, otherwise release handles.
            for connection in connections.all():
                if not connection.in_atomic_block:
                    connection.close()

    async def handle(request):
        return await dispatch(request)
    return handle
