"""Current owner's bounded client metadata and shared audited write revision."""
import uuid

from django.db import transaction
from django.db.models import Count, F

from . import services
from .models import ClientCatalogEvent, ClientCatalogState, Engagement, Entity, CATALOG_ACTIONS
from .schema import fields, require, text


class CatalogUnavailable(Exception):
    """No database, submitted name or private scope diagnostics cross HTTP."""


def identity(value):
    require(type(value) is str and len(value) == 36)
    try:
        require(str(uuid.UUID(value)) == value)
    except ValueError:
        require(False)
    return value


def current(user):
    member = services.scope(user, True)
    state, _ = ClientCatalogState.objects.get_or_create(user=user,
        defaults={'installation': member.installation})
    if state.installation_id != member.installation_id:
        raise services.ScopeDenied()
    return state


def serialize(user):
    """Take the metadata write boundary before any transactional catalog read.

    A deferred SQLite read in each competing transaction can leave both unable
    to upgrade the shared table lock. This exact no-op UPDATE is the first
    database statement of the metadata transaction, including first use before
    a catalog row exists. It changes no revision or audit; fresh authority,
    original identity and the real revision CAS still follow under that lock.
    """
    ClientCatalogState.objects.filter(user_id=user.pk).update(revision=F('revision'))


def claim(user, request=None):
    """Must be called inside the caller's actual metadata transaction."""
    state = current(user)
    expected = state.revision
    if request is not None:
        original, installation, expected = request
        if original != str(state.identity) or installation != str(state.installation_id):
            raise services.RevisionConflict()
    if type(expected) is not int or not 0 <= expected < 1_999_999_999:
        raise services.RevisionConflict()
    changed = ClientCatalogState.objects.filter(pk=state.pk, revision=expected,
        user__is_active=True, user__membership__enabled=True,
        user__membership__installation_id=state.installation_id,
        user__membership__role__in=('administrator', 'preparer')).update(revision=F('revision')+1)
    if changed != 1:
        raise services.RevisionConflict()
    state.revision = expected+1
    return state


def record_event(state, action, target_id, engagement_id):
    return ClientCatalogEvent.objects.create(catalog=state, revision=state.revision,
        action=action, target_id=target_id, engagement_id=engagement_id)


@transaction.atomic
def snapshot(user, selected=None):
    if selected is not None:
        selected = identity(selected)
    state = current(user)
    rows = list(services.owned_engagements(user, True).annotate(entity_count=Count('entities')).order_by('created_at', 'id')[:101])
    if len(rows) > 100 or any(row.entity_count > 100 for row in rows):
        raise CatalogUnavailable()
    if selected is None:
        chosen = rows[0] if rows else None
    else:
        chosen = next((row for row in rows if str(row.pk) == selected), None)
        if chosen is None:
            raise services.NotFound()
    entities = [] if chosen is None else list(chosen.entities.order_by('created_at', 'id')[:101])
    if len(entities) > 100:
        raise CatalogUnavailable()
    events = ClientCatalogEvent.objects.filter(catalog=state).order_by('-revision')[:20]
    return {'actor_id': str(user.pk), 'identity': str(state.identity), 'installation_id': str(state.installation_id),
        'revision': state.revision,
        'engagements': [{'id': str(row.pk), 'name': row.name, 'entity_count': row.entity_count} for row in rows],
        'selected_engagement_id': str(chosen.pk) if chosen else None,
        'entities': [{'id': str(row.pk), 'name': row.name} for row in entities],
        'events': [{'revision': row.revision, 'action': row.action, 'target_id': str(row.target_id),
                    'engagement_id': str(row.engagement_id), 'at': row.at} for row in events]}


@transaction.atomic
def apply(user, actor, body):
    serialize(user)
    services.scope(user, True)
    if actor != str(user.pk):
        raise services.RevisionConflict()
    action = body.get('action')
    require(type(action) is str and action in CATALOG_ACTIONS)
    arguments = {'create_engagement': ('name', 'entity_name'), 'create_entity': ('engagement_id', 'name'),
                 'rename_engagement': ('engagement_id', 'name'), 'rename_entity': ('entity_id', 'name')}[action]
    fields(body, ('identity', 'installation_id', 'expected_revision', 'action', *arguments))
    request = (identity(body['identity']), identity(body['installation_id']), body['expected_revision'])
    require(type(request[2]) is int and 0 <= request[2] < 2_000_000_000)
    for key in arguments:
        if key.endswith('_id'):
            identity(body[key])
        else:
            text(body[key])
    if action == 'create_engagement':
        row = services.create_engagement(user, {'name': body['name'], 'entity_name': body['entity_name']}, catalog_request=request)
        target, group = row.pk, row.pk
    elif action == 'create_entity':
        row = services.create_entity(user, body['engagement_id'], {'name': body['name']}, catalog_request=request)
        target, group = row.pk, row.engagement_id
    else:
        state = claim(user, request)
        if action == 'rename_engagement':
            row = services.owned_engagements(user, True).filter(pk=body['engagement_id']).first()
            if row is None:
                raise services.NotFound()
            Engagement.objects.filter(pk=row.pk).update(name=body['name'])
            target, group = row.pk, row.pk
        else:
            row = services.owned_entity(user, body['entity_id'], True)
            Entity.objects.filter(pk=row.pk).update(name=body['name'])
            target, group = row.pk, row.engagement_id
        record_event(state, action, target, group)
    state = current(user)
    return {'status': 'saved', 'actor_id': str(user.pk), 'identity': str(state.identity),
        'installation_id': str(state.installation_id), 'revision': state.revision, 'action': action,
        'target_id': str(target), 'engagement_id': str(group)}
