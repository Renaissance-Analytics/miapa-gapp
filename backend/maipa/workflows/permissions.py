"""Explicit human-owned, nonfinancial authorization of an original UI connection.

This is not the operator root factory. Closed HTTP commands select only enabled
or revoked; the server derives owner, principal, entity, duties and expiry.
Neither authoring tools nor graph/config values can call this lifecycle.
"""
from datetime import timedelta
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from backend.maipa.agent_accounts import ui
from backend.maipa.agent_accounts.models import UiComposition
from backend.maipa.policies import authority
from backend.maipa.policies.models import AgentPrincipal,AuthorityGrant,PolicyEpoch
from backend.maipa.intake.models import Entity
from . import services,drafts,native
from .models import NativePrincipalBinding
from .errors import Unavailable,Conflict
from .schema import require

OPERATIONS={'workflow.run':'executor','workflow.cancel':'executor',
    'workflow.resume':'executor','workflow.triage':'reviewer'}


def _current(user,draft_id,account_id,ui_id):
    # Same lock order as the original native broker: UI, installation, policy.
    account_id,ui_id=services.identity(account_id),services.identity(ui_id)
    UiComposition.objects.filter(pk=ui_id,session__user_id=user.pk,
        session__account_id=account_id).update(revision=F('revision'))
    member=services._principal(user)
    try:saved=ui._lookup(user,account_id,ui_id)
    except (ObjectDoesNotExist,ValueError,TypeError,OSError):raise Unavailable() from None
    current=drafts.get(user,draft_id);visible=[]
    def visit(node):
        if node['type']=='Maipa.Workflow':visible.append(node['props']['draft_id'])
        for child in node.get('children',[]):visit(child)
    try:composition=ui.ui_snapshot(user,account_id,ui_id)['composition']
    except ui.UiUnavailable:raise Unavailable() from None
    if composition:visit(composition['schema'])
    if visible!=[draft_id] or current['entity_id'] is None:raise Unavailable()
    entity=Entity.objects.select_related('engagement').get(pk=current['entity_id'])
    rows=list(NativePrincipalBinding.objects.select_related('agent').filter(
        owner_id=member.user_id,account_id=account_id,ui_id=ui_id)[:2])
    if len(rows)>1:raise Unavailable()
    binding=rows[0] if rows else None
    if binding:native._binding(user,binding)
    return member,saved,current,entity,binding


def _grants(binding,entity):
    if binding is None:return []
    return list(AuthorityGrant.objects.select_related('entity__engagement').filter(
        subject_id=binding.owner_id,agent_id=binding.agent_id,entity=entity,
        action__in=OPERATIONS,revoked_at__isnull=True))


def _enabled(rows):
    return all(any(row.action==action and row.duty==duty and authority.grant_is_current(row.pk)
        for row in rows) for action,duty in OPERATIONS.items())


def _snapshot(member,saved,current,entity,binding):
    rows=_grants(binding,entity);enabled=_enabled(rows)
    expires=min([binding.expires_at]+[row.expires_at for row in rows if authority.grant_is_current(row.pk)]) if binding else None
    return {'draft_id':current['id'],'draft_revision':current['revision'],
        'account_id':str(saved.session.account_id),'ui_id':str(saved.pk),
        'entity_id':str(entity.pk),'entity_name':entity.name,
        'policy_version':PolicyEpoch.objects.filter(entity=entity).values_list('version',flat=True).first() or 0,
        'can_manage':member.role=='administrator','enabled':enabled,'expires_at':expires}


@transaction.atomic
def snapshot(user,draft_id,account_id,ui_id):
    return _snapshot(*_current(user,draft_id,account_id,ui_id))


@transaction.atomic
def change(user,draft_id,account_id,ui_id,*,expected_revision,expected_policy_version,enabled):
    require(type(enabled) is bool)
    expected_revision=services._revision(expected_revision)
    expected_policy_version=services._revision(expected_policy_version)
    member,saved,current,entity,binding=_current(user,draft_id,account_id,ui_id)
    if member.role!='administrator':raise Unavailable()
    # Serializes grants/revocations with worker policy checks, including first use.
    version=authority._epoch(entity)
    if current['revision']!=expected_revision or version!=expected_policy_version:raise Conflict()
    if enabled and binding is None:
        principal=AgentPrincipal.objects.create(user=user,installation_id=member.installation_id,
            reference='Human-authorized workflow connection',expires_at=timezone.now()+timedelta(hours=1))
        identity=native.bind_principal(user,account_id,ui_id,principal)
        binding=NativePrincipalBinding.objects.select_related('agent').get(pk=identity)
    rows=_grants(binding,entity);changed=[]
    if enabled:
        for action,duty in OPERATIONS.items():
            if any(row.action==action and row.duty==duty and authority.grant_is_current(row.pk) for row in rows):continue
            now=timezone.now()
            expiry=min(binding.expires_at,now+timedelta(hours=1))
            try:authority._validate_grant(entity,user.pk,binding.agent_id,action,duty,now,
                expiry,None,None,'Human authorized this original workflow connection',False)
            except authority.Denied:raise Unavailable() from None
            row=AuthorityGrant.objects.create(entity=entity,subject=user,agent_id=binding.agent_id,
                action=action,duty=duty,starts_at=now,expires_at=expiry,
                evidence='Human authorized this original workflow connection',can_delegate=False)
            changed.append(row.pk)
        if changed:version=authority._epoch(entity,True)
    else:
        for row in rows:
            authority.revoke(authority.human(user),row.pk,'Human disabled workflow execution for this connection')
            changed.append(row.pk)
        version=authority._epoch(entity)
    if changed:authority._audit(None,entity,authority.actor_key(authority.human(user)),
        'workflow.authority.enable' if enabled else 'workflow.authority.disable',True,'recorded',version,changed)
    return _snapshot(member,saved,current,entity,binding)
