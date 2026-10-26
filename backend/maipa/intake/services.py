"""Server-owned scope, optimistic writes, dated resolution and dependency invalidation."""
from datetime import date
import uuid

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from backend.maipa.workspace.policy import membership
from backend.maipa.policies.access import intake_member
from backend.maipa.policies.authority import Denied
from .models import Conclusion, Engagement, Entity, FactDependency, FactRevision
from .schema import InvalidFact, assertion, fields, require, text


class ScopeDenied(Exception):
    def __init__(self, code='permission_denied'):
        self.code = code


class NotFound(Exception):
    pass


class RevisionConflict(Exception):
    pass


def scope(user, write=False):
    try:
        return intake_member(user, write)
    except Denied:
        raise ScopeDenied('workspace_access_denied' if membership(user) is None else 'permission_denied') from None


def owned_engagements(user, write=False):
    member = scope(user, write)
    return Engagement.objects.filter(owner=user, installation=member.installation)


def owned_entity(user, entity_id, write=False):
    row = Entity.objects.select_related('engagement').filter(pk=entity_id, engagement__in=owned_engagements(user, write)).first()
    if row is None:
        raise NotFound()
    return row


@transaction.atomic
def create_engagement(user, body, *, catalog_request=None):
    fields(body, ('name', 'entity_name')); text(body['name']); text(body['entity_name'])
    from .catalog import claim, record_event, serialize
    serialize(user)
    member = scope(user, True)
    state = claim(user, catalog_request)
    # Explicit small-install quotas keep complete initial lists bounded.
    require(owned_engagements(user).count() < 100)
    row = Engagement.objects.create(owner=user, installation=member.installation, name=body['name'])
    Entity.objects.create(engagement=row, name=body['entity_name'])
    record_event(state, 'create_engagement', row.pk, row.pk)
    return row


@transaction.atomic
def create_entity(user, engagement_id, body, *, catalog_request=None):
    fields(body, ('name',)); text(body['name'])
    from .catalog import claim, record_event, serialize
    serialize(user)
    state = claim(user, catalog_request)
    row = owned_engagements(user, True).select_for_update().filter(pk=engagement_id).first()
    if row is None:
        raise NotFound()
    require(row.entities.count() < 100)
    entity = Entity.objects.create(engagement=row, name=body['name'])
    record_event(state, 'create_entity', entity.pk, row.pk)
    return entity


def applicable(entity, on):
    # Recorded revision wins only among assertions effective on this day.
    rows = FactRevision.objects.filter(entity=entity, effective_from__lte=on).filter(
        Q(effective_to__isnull=True) | Q(effective_to__gt=on)).order_by('-revision')
    # Fourteen bounded indexed lookups avoid loading an unbounded revision history.
    from .schema import KEYS
    selected = {key: rows.filter(key=key).first() for key in KEYS}
    return {key: row for key, row in selected.items() if row is not None}


@transaction.atomic
def record_fact(user, entity_id, body):
    assertion(body)
    entity = owned_entity(user, entity_id, True)
    claimed = Entity.objects.filter(pk=entity.pk, revision=body['expected_revision']).update(revision=F('revision')+1)
    if not claimed:
        raise RevisionConflict()
    row = FactRevision.objects.create(entity=entity, key=body['key'], revision=body['expected_revision']+1,
        knowledge=body['knowledge'], value=body['value'], effective_from=body['effective_from'],
        effective_to=body['effective_to'], evidence=body['evidence'], recorded_by=user)
    # Conservative invalidation includes backdated/future corrections; never silently reuse a changed basis.
    Conclusion.objects.filter(entity=entity, status='current', dependencies__fact_revision__key=row.key).update(
        status='stale', invalidated_at=timezone.now())
    return row


@transaction.atomic
def record_conclusion(user, entity_id, label, revision_ids, on):
    """Internal workpaper seam; contacts/labels never grant application authority."""
    try:
        entity = owned_entity(user, entity_id, True)
        text(label); require(type(on) is date)
        require(isinstance(revision_ids, list) and 0 < len(revision_ids) <= 14)
        ids = [uuid.UUID(str(value)) for value in revision_ids]
        require(len(set(ids)) == len(ids))
        current = applicable(entity, on)
        selected = {row.pk: row for row in current.values() if row.knowledge == 'known'}
        require(all(identity in selected for identity in ids))
        # A no-op CAS takes the same write boundary as fact recording. A concurrent
        # entity revision must retry instead of introducing a current stale basis.
        require(Entity.objects.filter(pk=entity.pk, revision=entity.revision).update(revision=F('revision')) == 1)
        row = Conclusion.objects.create(entity=entity, label=label, as_of=on, recorded_by=user)
        FactDependency.objects.bulk_create([FactDependency(conclusion=row, fact_revision=selected[identity]) for identity in ids])
        return row
    except (ScopeDenied, NotFound, ValueError, TypeError, AttributeError):
        raise InvalidFact('Invalid conclusion basis') from None
