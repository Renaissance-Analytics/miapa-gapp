"""Explicit authority and reusable exact-snapshot decisions, with no financial dispatch.

Factories are trusted Python entrypoints. HTTP adapters derive humans only from
the authenticated session; agents come from server-owned registrations, never hints.
The eventual dispatcher must check again inside its own durable execution boundary.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import re
import uuid

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from backend.maipa.intake.models import Entity
from backend.maipa.workspace.policy import membership
from .models import (AgentPrincipal, Attestation, Authorization, AuthorityGrant,
                     GrantRevocation, PolicyAudit, PolicyEpoch, PolicyResource)

# Explicit closed registry. Adding an operational adapter is a separate reviewed step.
FINANCIAL = frozenset(('ledger.post', 'period.reopen', 'vendor.payment_details.change',
                      'payment.release', 'filing.submit', 'report.release'))
MONETARY = frozenset(('ledger.post', 'payment.release'))
WORKFLOW = frozenset('workflow.'+verb for verb in ('discover', 'create', 'edit', 'triage', 'run', 'resume', 'retry', 'cancel'))
ACTIONS = FINANCIAL | WORKFLOW
DUTIES = frozenset(('preparer', 'reviewer', 'approver', 'executor'))
_SEAL = object()


class Denied(Exception):
    """Intentionally generic: no foreign identity or submitted value is reflected."""


def require(condition):
    if not condition:
        raise Denied()


@dataclass(frozen=True)
class _Context:
    user_id: int
    agent_id: uuid.UUID | None
    seal: object


@dataclass(frozen=True)
class Decision:
    allowed: bool
    code: str
    audit_id: str


def _current(context):
    require(type(context) is _Context and context.seal is _SEAL)
    user = get_user_model().objects.filter(pk=context.user_id, is_active=True).first()
    require(user is not None)
    member = membership(user)
    require(member is not None)
    if context.agent_id is not None:
        require(AgentPrincipal.objects.filter(pk=context.agent_id, user=user,
            installation=member.installation, enabled=True, expires_at__gt=timezone.now()).exists())
    return member


def human(user):
    require(getattr(user, 'is_authenticated', False) and getattr(user, 'pk', None) is not None)
    context = _Context(user.pk, None, _SEAL)
    _current(context)
    return context


def agent(user, principal_id):
    try:
        context = _Context(user.pk, uuid.UUID(str(principal_id)), _SEAL)
        _current(context)
        return context
    except (ValueError, TypeError, AttributeError):
        raise Denied() from None


def actor_key(context):
    _current(context)
    return f'agent:{context.agent_id}' if context.agent_id else f'human:{context.user_id}'


def _actor(key, installation_id):
    require(isinstance(key, str) and len(key) <= 64)
    if key.startswith('human:'):
        identity = key[6:]
        require(identity.isascii() and identity.isdigit() and len(identity) <= 20)
        context = _Context(int(identity), None, _SEAL)
    elif key.startswith('agent:'):
        try:
            row = AgentPrincipal.objects.get(pk=uuid.UUID(key[6:]))
        except (ValueError, AgentPrincipal.DoesNotExist):
            raise Denied() from None
        context = _Context(row.user_id, row.pk, _SEAL)
    else:
        raise Denied()
    require(_current(context).installation_id == installation_id)
    require(actor_key(context) == key)
    return context


def _text(value, maximum=256):
    require(isinstance(value, str) and 0 < len(value) <= maximum and value == value.strip())
    require(not any(ord(char) < 32 for char in value))
    return value


def _money(value):
    require(isinstance(value, str) and re.fullmatch(r'(?:0|[1-9][0-9]{0,13})(?:\.[0-9]{1,4})?', value) is not None)
    return Decimal(value)


def _currency(value):
    require(isinstance(value, str) and re.fullmatch(r'[A-Z]{3}', value) is not None)


def _interval(start, end):
    require(type(start) is datetime and type(end) is datetime and timezone.is_aware(start) and timezone.is_aware(end))
    require(start < end)


def _date(value):
    require(isinstance(value, str) and len(value) <= 40)
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        raise Denied() from None
    require(timezone.is_aware(result))
    return result


def _json(value, depth=0):
    require(depth <= 16)
    if value is None or type(value) in (bool, int, str):
        if type(value) is int:
            require(abs(value) <= 9_007_199_254_740_991)
        if type(value) is str:
            require(len(value) <= 32768)
        return
    if type(value) is list:
        require(len(value) <= 1000)
        for item in value:
            _json(item, depth+1)
        return
    require(type(value) is dict and len(value) <= 1000)
    for key, item in value.items():
        _text(key, 128); _json(item, depth+1)


def canonical(value):
    _json(value)
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)
    require(len(raw.encode()) <= 65536)
    return raw


def _entity(context, entity_id):
    member = _current(context)
    try:
        row = Entity.objects.select_related('engagement').filter(pk=uuid.UUID(str(entity_id)),
            engagement__installation_id=member.installation_id).first()
    except (ValueError, TypeError):
        raise Denied() from None
    require(row is not None)
    return row


def _epoch(entity, bump=False):
    row, _ = PolicyEpoch.objects.get_or_create(entity=entity)
    # Also obtains SQLite's write boundary; no stale check may race a revocation.
    PolicyEpoch.objects.filter(pk=row.pk).update(version=F('version') + (1 if bump else 0))
    return PolicyEpoch.objects.get(pk=row.pk).version


def _validate_grant(entity, subject_id, agent_id, action, duty, starts_at, expires_at, limit, currency, evidence, can_delegate):
    require(action in ACTIONS and duty in DUTIES and type(can_delegate) is bool)
    _interval(starts_at, expires_at); _text(evidence)
    context = _actor(f'agent:{agent_id}' if agent_id else f'human:{subject_id}', entity.engagement.installation_id)
    require(context.user_id == subject_id and _current(context).role != 'viewer')
    if agent_id:
        require(not can_delegate and (duty in ('preparer', 'executor') or
            (duty == 'reviewer' and action in ('workflow.discover', 'workflow.triage'))))
    if action in MONETARY:
        _money(limit); _currency(currency)
    else:
        require(limit is None and currency is None)
    return context


@transaction.atomic
def provision_root(*, entity_id, subject_id, action, duty, starts_at, expires_at, limit, currency, evidence,
                   can_delegate=False, agent_id=None):
    """Trusted local-operator provisioning ONLY. Never expose this factory over HTTP/tools."""
    try:
        entity = Entity.objects.select_related('engagement').get(pk=entity_id)
    except (Entity.DoesNotExist, ValueError):
        raise Denied() from None
    _validate_grant(entity, subject_id, agent_id, action, duty, starts_at, expires_at, limit, currency, evidence, can_delegate)
    version = _epoch(entity, True)
    row = AuthorityGrant.objects.create(entity=entity, subject_id=subject_id, agent_id=agent_id,
        action=action, duty=duty, starts_at=starts_at, expires_at=expires_at, limit=limit,
        currency=currency, evidence=evidence, can_delegate=can_delegate)
    _audit(None, entity, 'operator', 'grant.root', True, 'recorded', version, [row.pk])
    return row


def _live(row, seen=None):
    seen = set() if seen is None else seen
    require(row.pk not in seen and len(seen) < 2)
    seen.add(row.pk)
    now = timezone.now()
    require(row.revoked_at is None and row.starts_at <= now < row.expires_at)
    context = _validate_grant(row.entity, row.subject_id, row.agent_id, row.action, row.duty,
        row.starts_at, row.expires_at, row.limit, row.currency, row.evidence, row.can_delegate)
    if row.parent_id:
        parent = AuthorityGrant.objects.select_related('entity__engagement').get(pk=row.parent_id)
        _live(parent, seen)
        require(parent.parent_id is None and parent.can_delegate and not row.can_delegate)
        require((row.entity_id, row.action, row.duty, row.currency, row.delegated_by_id) ==
                (parent.entity_id, parent.action, parent.duty, parent.currency, parent.subject_id))
        require(parent.starts_at <= row.starts_at < row.expires_at <= parent.expires_at)
        require(row.subject_id != parent.subject_id and (row.limit is None or _money(row.limit) <= _money(parent.limit)))
    return context


def grant_is_current(grant_id):
    try:
        _live(AuthorityGrant.objects.select_related('entity__engagement').get(pk=grant_id))
        return True
    except (Denied, AuthorityGrant.DoesNotExist, ValueError):
        return False


@transaction.atomic
def delegate(context, parent_id, *, subject_id, starts_at, expires_at, limit, currency, evidence):
    member = _current(context)
    require(context.agent_id is None)
    parent = AuthorityGrant.objects.select_related('entity__engagement').filter(pk=parent_id,
        subject_id=context.user_id, agent__isnull=True, entity__engagement__installation_id=member.installation_id).first()
    require(parent is not None)
    _epoch(parent.entity)
    _live(parent)
    require(parent.can_delegate and parent.parent_id is None and subject_id != context.user_id)
    _validate_grant(parent.entity, subject_id, None, parent.action, parent.duty, starts_at, expires_at,
        limit, currency, evidence, False)
    require(parent.starts_at <= starts_at < expires_at <= parent.expires_at and currency == parent.currency)
    require(limit is None or _money(limit) <= _money(parent.limit))
    version = _epoch(parent.entity, True)
    row = AuthorityGrant.objects.create(entity=parent.entity, subject_id=subject_id, action=parent.action,
        duty=parent.duty, starts_at=starts_at, expires_at=expires_at, limit=limit, currency=currency,
        evidence=evidence, parent=parent, delegated_by_id=context.user_id, can_delegate=False)
    _audit(None, row.entity, actor_key(context), 'grant.delegate', True, 'recorded', version, [parent.pk, row.pk])
    return row


@transaction.atomic
def revoke(context, grant_id, reason):
    member = _current(context)
    require(context.agent_id is None); _text(reason)
    row = AuthorityGrant.objects.select_related('entity__engagement').filter(pk=grant_id,
        entity__engagement__installation_id=member.installation_id).first()
    require(row is not None and context.user_id in (row.subject_id, row.delegated_by_id))
    _epoch(row.entity)
    if row.revoked_at is None:
        now = timezone.now()
        require(AuthorityGrant.objects.filter(pk=row.pk, revoked_at__isnull=True).update(revoked_at=now) == 1)
        GrantRevocation.objects.create(grant=row, actor=actor_key(context), reason=reason)
        version = _epoch(row.entity, True)
        _audit(None, row.entity, actor_key(context), 'grant.revoke', True, 'revoked', version, [row.pk])


@transaction.atomic
def operator_revoke(grant_id, reason):
    """Trusted local command recovery, including a disabled or departed subject."""
    _text(reason)
    row = AuthorityGrant.objects.select_related('entity__engagement').filter(pk=grant_id).first()
    require(row is not None)
    _epoch(row.entity)
    if row.revoked_at is None:
        require(AuthorityGrant.objects.filter(pk=row.pk, revoked_at__isnull=True).update(revoked_at=timezone.now()) == 1)
        GrantRevocation.objects.create(grant=row, actor='operator', reason=reason)
        version = _epoch(row.entity, True)
        _audit(None, row.entity, 'operator', 'grant.revoke', True, 'revoked', version, [row.pk])


def _find(context, entity, action, duty, amount=None, currency=None):
    require(_current(context).installation_id == entity.engagement.installation_id)
    for row in AuthorityGrant.objects.select_related('entity__engagement').filter(entity=entity,
            subject_id=context.user_id, agent_id=context.agent_id, action=action, duty=duty).order_by('created_at', 'id'):
        try:
            _live(row)
            require(amount is None or (row.currency == currency and _money(amount) <= _money(row.limit)))
            return row
        except Denied:
            continue
    raise Denied()


SNAPSHOT_KEYS = frozenset(('entity_id', 'action', 'entity_revision', 'record_revision', 'graph_revision',
    'resource_id', 'payload', 'amount', 'currency', 'evidence', 'executor', 'starts_at', 'expires_at'))


def _snapshot(context, value):
    require(type(value) is dict and set(value) == SNAPSHOT_KEYS)
    canonical(value)
    require(value['action'] in FINANCIAL)
    entity = _entity(context, value['entity_id'])
    require(str(entity.pk) == value['entity_id'])
    require(type(value['entity_revision']) is int and value['entity_revision'] == entity.revision)
    _text(value['record_revision'], 128)
    require(type(value['payload']) is dict)
    if value['action'] in MONETARY:
        _money(value['amount']); _currency(value['currency'])
    else:
        require(value['amount'] is None and value['currency'] is None)
    require(type(value['evidence']) is list and 0 < len(value['evidence']) <= 20)
    for reference in value['evidence']:
        _text(reference)
    start, end = _date(value['starts_at']), _date(value['expires_at'])
    _interval(start, end); require(start <= timezone.now() < end)
    _actor(value['executor'], entity.engagement.installation_id)
    if value['resource_id'] is None:
        require(value['graph_revision'] is None)
    else:
        # Independent financial reviewers may differ from the workflow owner.
        # Their exact entity/action/duty grant is checked before any decision.
        resource = _resource(context, value['resource_id'], owner=False)
        require(resource.entity_id == entity.pk and resource.graph_revision == value['graph_revision'])
    return entity


def _hash(snapshot, preparer, version, resource_revision):
    return hashlib.sha256(canonical(dict(snapshot=snapshot, preparer=preparer, policy_version=version,
        resource_revision=resource_revision)).encode()).hexdigest()


def _matching(context, authorization_id, snapshot):
    entity = _snapshot(context, snapshot)
    row = Authorization.objects.filter(pk=authorization_id, entity=entity).select_related('entity__engagement', 'preparer_grant').first()
    require(row is not None)
    version = _epoch(entity)
    require(version == row.policy_version)
    resource_revision = _resource(context, snapshot['resource_id'], owner=False).revision if snapshot['resource_id'] else None
    require(resource_revision == row.resource_revision)
    require(_hash(snapshot, row.preparer, version, resource_revision) == row.snapshot_hash)
    require(_hash(row.snapshot, row.preparer, version, resource_revision) == row.snapshot_hash)
    preparer = _actor(row.preparer, entity.engagement.installation_id)
    _bound_grant(row.preparer_grant, preparer, snapshot, 'preparer')
    return row


def _bound_grant(grant, context, snapshot, duty):
    _live(grant)
    require((grant.subject_id, grant.agent_id, str(grant.entity_id), grant.action, grant.duty, grant.currency) ==
        (context.user_id, context.agent_id, snapshot['entity_id'], snapshot['action'], duty, snapshot['currency']))
    require(grant.limit is None if snapshot['amount'] is None else _money(snapshot['amount']) <= _money(grant.limit))


def _attestations(row):
    items = list(Attestation.objects.filter(authorization=row).select_related('grant__entity__engagement'))
    for item in items:
        context = _actor(item.actor, row.entity.engagement.installation_id)
        require(context.agent_id is None and context.user_id != _actor(row.preparer, row.entity.engagement.installation_id).user_id)
        require(item.snapshot_hash == row.snapshot_hash)
        _bound_grant(item.grant, context, row.snapshot, item.duty)
    return {item.duty: item for item in items}


@transaction.atomic
def prepare(context, snapshot):
    entity = _snapshot(context, snapshot)
    version = _epoch(entity)
    grant = _find(context, entity, snapshot['action'], 'preparer', snapshot['amount'], snapshot['currency'])
    key = actor_key(context)
    resource_revision = _resource(context, snapshot['resource_id'], owner=False).revision if snapshot['resource_id'] else None
    row = Authorization.objects.create(entity=entity, preparer=key, preparer_grant=grant,
        policy_version=version, resource_revision=resource_revision,
        snapshot=json.loads(canonical(snapshot)), snapshot_hash=_hash(snapshot, key, version, resource_revision))
    _audit(row, entity, key, 'authorization.prepare', True, 'recorded', version, [grant.pk])
    return row


@transaction.atomic
def attest(context, authorization_id, snapshot, duty):
    require(duty in ('reviewer', 'approver') and context.agent_id is None)
    row = _matching(context, authorization_id, snapshot)
    require(context.user_id != _actor(row.preparer, row.entity.engagement.installation_id).user_id)
    existing = _attestations(row)
    require(duty != 'approver' or 'reviewer' in existing)
    grant = _find(context, row.entity, snapshot['action'], duty, snapshot['amount'], snapshot['currency'])
    if duty in existing:
        require(existing[duty].actor == actor_key(context))
        return existing[duty]
    item = Attestation.objects.create(authorization=row, actor=actor_key(context), duty=duty,
        grant=grant, snapshot_hash=row.snapshot_hash)
    _audit(row, row.entity, item.actor, 'authorization.'+duty, True, 'recorded', row.policy_version, [grant.pk])
    return item


def _audit(row, entity, actor, operation, allowed, code, version=0, grants=()):
    return PolicyAudit.objects.create(authorization=row, entity=entity, actor=actor, operation=operation,
        allowed=allowed, code=code, snapshot_hash=row.snapshot_hash if row else '', policy_version=version,
        grant_ids=[str(identity) for identity in grants])


@transaction.atomic
def check_execution(context, authorization_id, snapshot):
    row = None
    actor = 'unverified'
    grants = []
    try:
        actor = actor_key(context)
        row = _matching(context, authorization_id, snapshot)
        require(actor == snapshot['executor'])
        items = _attestations(row)
        require(set(items) == {'reviewer', 'approver'})
        grant = _find(context, row.entity, snapshot['action'], 'executor', snapshot['amount'], snapshot['currency'])
        grants = [row.preparer_grant_id, *(item.grant_id for item in items.values()), grant.pk]
        allowed = True
    except (Denied, ValueError, TypeError, AttributeError):
        allowed = False
    event = _audit(row, row.entity if row else None, actor, 'execution.check', allowed,
        'authorized' if allowed else 'authority_denied', row.policy_version if row else 0, grants)
    return Decision(event.allowed, event.code, str(event.pk))


def execute(context, authorization_id, snapshot):
    """No side effects exist in this foundation, including externally facing actions."""
    decision = check_execution(context, authorization_id, snapshot)
    return Decision(False, 'execution_not_available' if decision.allowed else decision.code, decision.audit_id)


def _resource(context, resource_id, owner=True):
    member = _current(context)
    try:
        rows = PolicyResource.objects.select_related('entity__engagement').filter(pk=uuid.UUID(str(resource_id)),
            entity__engagement__installation_id=member.installation_id)
        if owner:
            rows = rows.filter(owner_id=context.user_id)
        row = rows.first()
    except (ValueError, TypeError):
        raise Denied() from None
    require(row is not None and row.enabled)
    seen = set()
    child = row
    while child.parent_id:
        require(child.pk not in seen and len(seen) < 5)
        seen.add(child.pk)
        parent = PolicyResource.objects.filter(pk=child.parent_id, owner_id=row.owner_id,
            entity_id=row.entity_id, enabled=True, graph_revision=row.graph_revision).first()
        require(parent is not None)
        child = parent
    require(child.kind == 'graph' and child.graph_revision == row.graph_revision)
    return row


WORKFLOW_DUTY = {verb: 'preparer' if verb in ('create', 'edit') else
    'reviewer' if verb in ('discover', 'triage') else 'executor'
    for verb in ('discover', 'create', 'edit', 'triage', 'run', 'resume', 'retry', 'cancel')}


@transaction.atomic
def check_workflow(*, context, resource_id, operation, expected_revision, graph_revision,
                   stage, outcome, authorization_id=None, financial_snapshot=None):
    """Policy contract only. Actual Fancy Flow adapters must supply durable state.

    outcome is trusted persisted execution evidence from that future adapter,
    never an HTTP/agent assertion that a prior attempt had no effect.
    """
    resource = None
    actor = 'unverified'
    grants = []
    version = 0
    try:
        actor = actor_key(context)
        require(operation in WORKFLOW_DUTY and stage in ('dispatch', 'node', 'resume'))
        require(type(expected_revision) is int)
        resource = _resource(context, resource_id)
        require(resource.revision == expected_revision and resource.graph_revision == graph_revision)
        version = _epoch(resource.entity)
        grant = _find(context, resource.entity, 'workflow.'+operation, WORKFLOW_DUTY[operation])
        grants = [grant.pk]
        require(operation != 'retry' or outcome in ('not_started', 'verified_no_effect'))
        if authorization_id is not None or financial_snapshot is not None:
            require(authorization_id is not None and type(financial_snapshot) is dict)
            require(financial_snapshot.get('resource_id') == str(resource.pk)
                and financial_snapshot.get('graph_revision') == resource.graph_revision)
            require(check_execution(context, authorization_id, financial_snapshot).allowed)
        allowed = True
    except (Denied, ValueError, TypeError, AttributeError):
        allowed = False
    event = _audit(None, resource.entity if resource else None, actor, 'workflow.check', allowed,
        'authorized' if allowed else 'authority_denied', version, grants)
    return Decision(event.allowed, event.code, str(event.pk))
