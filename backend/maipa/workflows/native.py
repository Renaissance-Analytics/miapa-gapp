"""Internal native adapter: explicit original provenance and current typed policy.

No HTTP/MCP exposure, automatic principal/grant provisioning or durable handoff.
The trusted host binds an existing principal to its actual session/UI lease.
Only the deterministic executors admitted by services are available.
"""
from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction

from backend.maipa.agent_accounts.ui import _lookup
from backend.maipa.policies import authority
from backend.maipa.policies.models import AgentPrincipal, PolicyAudit, PolicyResource
from . import services
from .errors import Conflict, Unavailable
from .models import NativePrincipalBinding, NativeGraphBinding, NativeRunAuthority
from .schema import RUNTIME, validate

_SEAL=object()


@dataclass(frozen=True)
class _Actor:
    user_id: int
    binding_id: str
    seal: object


def _binding(user,binding):
    member=services._principal(user)
    if (binding.owner_id!=member.user_id or binding.installation_id!=member.installation_id
            or binding.agent.user_id!=member.user_id or binding.agent.installation_id!=member.installation_id
            or binding.agent.expires_at!=binding.expires_at): raise Unavailable()
    try:
        ui=_lookup(user,str(binding.account_id),str(binding.ui_id))
        if ui.session_id!=binding.session_id: raise Unavailable()
        context=authority.agent(user,binding.agent_id)
    except (ObjectDoesNotExist,ValueError,TypeError,AttributeError,OSError,authority.Denied):
        raise Unavailable() from None
    return member,context


def _resolve(context):
    if type(context) is not _Actor or context.seal is not _SEAL: raise Unavailable()
    user=get_user_model().objects.filter(pk=context.user_id,is_active=True).first()
    binding=NativePrincipalBinding.objects.select_related('agent').filter(
        pk=services.identity(context.binding_id),owner_id=context.user_id).first()
    if binding is None or user is None: raise Unavailable()
    member,policy=_binding(user,binding)
    return user,member,binding,policy


@transaction.atomic
def bind_principal(user,account_id,ui_id,principal):
    """Trusted host ONLY; principal creation/authority is a separate operator act."""
    member=services._principal(user)
    if not isinstance(principal,AgentPrincipal): raise Unavailable()
    current=AgentPrincipal.objects.filter(pk=principal.pk,user_id=member.user_id,
        installation_id=member.installation_id).first()
    if current is None: raise Unavailable()
    try:
        ui=_lookup(user,services.identity(account_id),services.identity(ui_id))
        authority.agent(user,current.pk)
    except (ObjectDoesNotExist,ValueError,TypeError,AttributeError,OSError,authority.Denied):
        raise Unavailable() from None
    old=NativePrincipalBinding.objects.select_related('agent').filter(agent=current).first()
    if old is not None:
        if (old.owner_id,old.installation_id,str(old.account_id),old.session_id,old.ui_id,old.expires_at)!=(
                member.user_id,member.installation_id,account_id,ui.session_id,ui.pk,current.expires_at): raise Conflict()
        _binding(user,old)
        return str(old.pk)
    row=NativePrincipalBinding.objects.create(owner_id=member.user_id,installation_id=member.installation_id,
        account_id=account_id,session_id=ui.session_id,ui=ui,agent=current,expires_at=current.expires_at)
    _binding(user,row)
    return str(row.pk)


@transaction.atomic
def actor(user,binding_id):
    member=services._principal(user)
    context=_Actor(member.user_id,services.identity(binding_id),_SEAL)
    _resolve(context)
    return context


def _graph(member,definition,version):
    binding=NativeGraphBinding.objects.select_related('resource').filter(version=version).first()
    _,digest,_=validate(version.schema)
    if binding is None or version.runtime!=RUNTIME or version.schema_sha256!=digest: raise Unavailable()
    resource=binding.resource
    if (resource.owner_id,resource.entity_id,resource.kind,resource.graph_revision,resource.revision,resource.parent_id)!=(
            member.user_id,definition.entity_id,'graph',digest,binding.resource_revision,None) or not resource.enabled:
        raise Unavailable()
    return binding


@transaction.atomic
def prepare_graph(user,definition_id,revision):
    """Register an actual immutable owned version; creates no authority grant."""
    member=services._principal(user);definition=services._definition(member,definition_id)
    if definition.entity_id is None: raise Unavailable()
    version=definition.versions.filter(revision=services._revision(revision)).first()
    if version is None: raise Conflict()
    _,digest,_=validate(version.schema)
    if version.runtime!=RUNTIME or version.schema_sha256!=digest: raise Unavailable()
    if not NativeGraphBinding.objects.filter(version=version).exists():
        resource=PolicyResource.objects.create(owner_id=member.user_id,entity_id=definition.entity_id,
            kind='graph',graph_revision=digest)
        NativeGraphBinding.objects.create(version=version,resource=resource,resource_revision=resource.revision)
    binding=_graph(member,definition,version)
    return {'definition_id':str(definition.pk),'revision':version.revision,'schema_sha256':digest,
            'resource_id':str(binding.resource_id),'resource_revision':binding.resource_revision}


def _check(policy,graph,operation,stage):
    decision=authority.check_workflow(context=policy,resource_id=graph.resource_id,operation=operation,
        expected_revision=graph.resource_revision,graph_revision=graph.version.schema_sha256,
        stage=stage,outcome='not_started')
    if not decision.allowed: raise Unavailable()
    event=PolicyAudit.objects.get(pk=decision.audit_id)
    if len(event.grant_ids)!=1: raise Unavailable()
    return event.grant_ids[0]


def _worker():
    from .worker import runtime_status
    status=runtime_status()
    if not status['available']: raise Unavailable()
    return status


@transaction.atomic
def dispatch(context,definition_id,revision,seed,dispatch_key):
    user,member,binding,policy=_resolve(context)
    definition=services._definition(member,definition_id)
    version=definition.versions.filter(revision=services._revision(revision)).first()
    if version is None: raise Conflict()
    graph=_graph(member,definition,version)
    grant_id=_check(policy,graph,'run','dispatch');status=_worker()
    run,created=services._dispatch_row(member,definition_id,revision,seed,dispatch_key,origin_kind='native')
    old=NativeRunAuthority.objects.filter(run=run).first()
    if old is not None:
        if (old.binding_id,old.graph_id,str(old.grant_id))!=(binding.pk,graph.pk,grant_id): raise Conflict()
    else:
        # A previously persisted native marker without evidence must never be
        # adopted. This transaction creates both records or neither record.
        if not created: raise Unavailable()
        NativeRunAuthority.objects.create(run=run,binding=binding,graph=graph,grant_id=grant_id)
    return dict(services._run_value(run),worker=status)


def _evidence(run,member,binding):
    evidence=NativeRunAuthority.objects.select_related('graph__version','grant').filter(run=run,binding=binding).first()
    if evidence is None or run.origin_kind!='native' or not services._origin_matches(run): raise Unavailable()
    if (run.origin_owner_id,run.origin_installation_id)!=(binding.owner_id,binding.installation_id): raise Unavailable()
    graph=_graph(member,run.definition,run.version)
    if evidence.graph_id!=graph.pk or graph.version_id!=run.version_id: raise Unavailable()
    grant=evidence.grant
    if (grant.subject_id,grant.agent_id,grant.entity_id,grant.action,grant.duty)!=(
            binding.owner_id,binding.agent_id,run.origin_entity_id,'workflow.run','executor'):
        raise Unavailable()
    if not authority.grant_is_current(grant.pk): raise Unavailable()
    return graph


def guard_run(run):
    """Inside the worker authority transaction; missing evidence is a refusal."""
    try:
        evidence=NativeRunAuthority.objects.select_related('binding__agent').filter(run=run).first()
        if evidence is None: return False
        user=get_user_model().objects.filter(pk=evidence.binding.owner_id,is_active=True).first()
        member,policy=_binding(user,evidence.binding)
        graph=_evidence(run,member,evidence.binding)
        _check(policy,graph,'run','node')
        return True
    except (Unavailable,authority.Denied,ObjectDoesNotExist,ValueError,TypeError,AttributeError,OSError):
        return False


@transaction.atomic
def resume(context,run_id,expected_step):
    user,member,binding,policy=_resolve(context)
    run=services._run(member,run_id)
    if run.step!=services._revision(expected_step) or run.status!='blocked': raise Conflict()
    graph=_evidence(run,member,binding)
    _check(policy,graph,'resume','resume');status=_worker()
    services._coordinator(run)
    run.status,run.error='queued',None;run.step+=1
    run.save(update_fields=('status','error','step'))
    return dict(services._run_value(run),worker=status)


@transaction.atomic
def get_run(context,run_id):
    user,member,binding,policy=_resolve(context)
    run=services._run(member,run_id)
    graph=_evidence(run,member,binding)
    _check(policy,graph,'triage','node')
    return services._run_value(run)


@transaction.atomic
def cancel(context,run_id,expected_step):
    user,member,binding,policy=_resolve(context)
    run=services._run(member,run_id)
    if run.step!=services._revision(expected_step):raise Conflict()
    graph=_evidence(run,member,binding)
    _check(policy,graph,'cancel','node')
    return services.cancel(user,run_id,expected_step)
