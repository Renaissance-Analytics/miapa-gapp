"""Original-session pending input, never a submission or business-write receipt.

Trusted application consumers supply actor/account/UI identity independently.
No HTTP route or native tool is registered by this storage boundary.
"""
from copy import deepcopy
from functools import wraps
import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.db.models import F

from . import ui
from .models import UiComposition
from .references import _uuid

MESSAGE = 'Form draft unavailable'
LIMIT = 1_999_999_998
NAMES = ('name', 'entity_name')
FIELDS = (
    {'name': 'name', 'label': 'Client / engagement name', 'type': 'text', 'required': True, 'max_length': 128},
    {'name': 'entity_name', 'label': 'Initial entity name', 'type': 'text', 'required': True, 'max_length': 128},
)


class FormUnavailable(Exception):
    """Closed failure with no input, path or private binding diagnostics."""


class FormConflict(FormUnavailable):
    """A newer pending value exists; read the current draft before editing."""


def _safe(operation):
    @wraps(operation)
    def call(*args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except (OSError, ValueError, TypeError, AttributeError, OverflowError, RecursionError,
                DatabaseError, ObjectDoesNotExist, ui.UiUnavailable):
            raise FormUnavailable(MESSAGE) from None
    return call


def _values(value, *, complete):
    if (type(value) is not dict or set(value) - set(NAMES) or
            (complete and set(value) != set(NAMES))):
        raise ValueError('Closed draft values required')
    for text in value.values():
        if (type(text) is not str or len(text) > 128 or
                any(ord(char) < 32 or ord(char) == 127 for char in text)):
            raise ValueError('Invalid pending value')
        text.encode('utf-8')
    return deepcopy(value)


def _state(value):
    if type(value) is not dict or len(value) > 1:
        raise ValueError('Invalid stored draft collection')
    for component, draft in value.items():
        if type(component) is not str or not ui.HANDLE.fullmatch(component):
            raise ValueError('Invalid component handle')
        if type(draft) is not dict or set(draft) != {'id', 'revision', 'values'}:
            raise ValueError('Invalid stored draft')
        _uuid(draft['id'])
        if type(draft['revision']) is not int or not 0 <= draft['revision'] <= LIMIT:
            raise ValueError('Invalid stored revision')
        _values(draft['values'], complete=True)
    return deepcopy(value)


def _catalog_handles(composition):
    handles = set()
    def visit(node):
        if node['type'] == 'Maipa.ClientCatalog':
            handles.add(node['id'])
        for child in node.get('children', []):
            visit(child)
    if composition:
        visit(composition['schema'])
    return handles


def retain_visible_drafts(current, composition):
    """Called inside a structural UI write: removed instances cannot resurrect."""
    handles = _catalog_handles(composition)
    if not handles:
        return {}
    # A surviving corrupted entry remains refused, never implicitly repaired.
    if type(current) is not dict:
        return current
    return {key: value for key, value in current.items() if key in handles}


def _locked(actor, account_id, identity, component_id):
    if (not isinstance(actor, get_user_model()) or actor.is_authenticated is not True or
            actor.pk is None):
        raise ValueError('Original actor required')
    if type(component_id) is not str or not ui.HANDLE.fullmatch(component_id):
        raise ValueError('Invalid component')
    UiComposition.objects.filter(pk=_uuid(identity), session__user_id=actor.pk,
        session__account_id=_uuid(account_id)).update(form_drafts=F('form_drafts'))
    # Rebind a stale Python object to the primary current user before authority.
    current = get_user_model().objects.get(pk=actor.pk, is_active=True)
    saved = ui._lookup(current, account_id, identity)
    composition = ui._snapshot(saved)['composition']
    handles = _catalog_handles(composition)
    values = _state(saved.form_drafts)
    if component_id not in handles or set(values) - handles:
        raise ValueError('Form is not present')
    return saved, values, current


def _snapshot(component_id, draft):
    return {'id': draft['id'], 'revision': draft['revision'], 'component_id': component_id,
            'kind': 'new-client', 'fields': deepcopy(list(FIELDS)), 'values': deepcopy(draft['values'])}


def _publish(actor, account_id, identity, values):
    UiComposition.objects.filter(pk=_uuid(identity), session__user_id=actor.pk,
        session__account_id=_uuid(account_id)).update(form_drafts=values)
    ui._lookup(actor, account_id, identity)


@_safe
def read_new_client_form(actor, account_id, identity, component_id):
    with transaction.atomic():
        saved, values, actor = _locked(actor, account_id, identity, component_id)
        if component_id not in values:
            values[component_id] = {'id': str(uuid.uuid4()), 'revision': 0,
                                    'values': {'name': '', 'entity_name': ''}}
            _publish(actor, account_id, identity, values)
        return _snapshot(component_id, values[component_id])


@_safe
def change_new_client_form(actor, account_id, identity, component_id, command):
    if type(command) is not dict or set(command) != {'id', 'expected_revision', 'values'}:
        raise ValueError('Closed draft operation required')
    # Capture/detach bounded inputs before database waits can release the GIL.
    original = _uuid(command['id'])
    revision = command['expected_revision']
    if type(revision) is not int or not 0 <= revision <= LIMIT:
        raise ValueError('Invalid draft revision')
    patch = _values(command['values'], complete=False)
    with transaction.atomic():
        saved, values, actor = _locked(actor, account_id, identity, component_id)
        draft = values.get(component_id)
        if not draft or draft['id'] != original:
            raise ValueError('Original draft instance changed')
        if draft['revision'] != revision:
            raise FormConflict(MESSAGE)
        updated = {**draft['values'], **patch}
        if updated != draft['values']:
            if revision >= LIMIT:
                raise ValueError('Draft revision exhausted')
            draft.update(revision=revision + 1, values=updated)
            _publish(actor, account_id, identity, values)
        return _snapshot(component_id, draft)
