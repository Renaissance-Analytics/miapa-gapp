"""Same authorized persistence boundary for future human and native Flow tools.

This foundation deliberately exposes no HTTP/MCP execution surface yet. Only
three deterministic published executors are bound. Queue transport, editor and
human gates are separate integration work, and no protected effect is enabled.
"""
import uuid

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F
from fancy_flow import ExecutorRegistry
from fancy_flow.durable import Coordinator, Frontier
from fancy_flow.nodes import logic, output, trigger

from backend.maipa.intake.models import Entity
from backend.maipa.workspace.models import Installation, Membership
from .errors import Conflict, InvalidWorkflow, OutputLimit, Unavailable
from .models import Definition, DefinitionVersion, WorkflowRun
from .schema import RUNTIME, REGISTRY, bounded_json, require, validate
from .store import DjangoClaimStore


def identity(value):
    try:
        require(type(value) is str and str(uuid.UUID(value))==value)
    except (ValueError,TypeError,AttributeError):
        raise Unavailable() from None
    return value


def _principal(user):
    # First statement takes the same boundary as local role administration.
    # Never trust cached User.is_active, staff flags or a submitted installation.
    Installation.objects.filter(slot=1).update(access_revision=F('access_revision'))
    if not isinstance(user,get_user_model()) or user.pk is None:
        raise Unavailable()
    member = Membership.objects.select_related('installation').filter(user_id=user.pk,user__is_active=True,
        enabled=True,role__in=('administrator','preparer'),installation__slot=1).first()
    if member is None:
        raise Unavailable()
    return member


def _entity(member, entity_id):
    if entity_id is not None and not Entity.objects.filter(pk=entity_id,engagement__owner_id=member.user_id,
            engagement__installation_id=member.installation_id).exists():
        raise Unavailable()


def _definition(member, definition_id):
    row = Definition.objects.filter(pk=identity(definition_id),owner_id=member.user_id,installation_id=member.installation_id).first()
    if row is None:
        raise Unavailable()
    _entity(member,row.entity_id)
    return row


def _run(member, run_id):
    run = WorkflowRun.objects.select_related('definition','version').filter(pk=identity(run_id),
        definition__owner_id=member.user_id,definition__installation_id=member.installation_id).first()
    if run is None or run.version.definition_id != run.definition_id:
        raise Unavailable()
    _entity(member,run.definition.entity_id)
    return run


def _revision(value):
    if type(value) is not int or not 0 <= value < 1_999_999_999:
        raise Conflict()
    return value


def _definition_value(row):
    version = row.versions.get(revision=row.revision)
    return {'id':str(row.pk),'name':row.name,'revision':row.revision,'entity_id':str(row.entity_id) if row.entity_id else None,
            'schema':version.schema,'schema_sha256':version.schema_sha256,'runtime':version.runtime}


def _run_value(run):
    state = DjangoClaimStore(run).state(str(run.pk))
    return {'id':str(run.pk),'definition_id':str(run.definition_id),'definition_revision':run.version.revision,
        'schema_sha256':run.version.schema_sha256,'runtime':run.version.runtime,'step':run.step,'status':run.status,'error':run.error,
        'outputs':{key:row.output for key,row in state.items() if row.status=='completed'},
        'nodes':{key:{'status':row.status,'attempts':row.attempts,'error':row.error,'ports':list(row.ports)} for key,row in state.items()}}


@transaction.atomic
def save_definition(user,name,schema,*,entity_id=None,definition_id=None,expected_revision=0):
    member = _principal(user)
    row = _definition(member,definition_id) if definition_id is not None else None
    require(type(name) is str and name==name.strip() and 0 < len(name) <=128 and not any(ord(c)<32 or ord(c)==127 for c in name))
    expected_revision = _revision(expected_revision)
    if entity_id is not None: entity_id=identity(entity_id)
    _entity(member,entity_id)
    schema,digest,_ = validate(schema)
    if row is None:
        if expected_revision != 0: raise Conflict()
        require(Definition.objects.filter(owner_id=member.user_id,installation_id=member.installation_id).count()<100)
        row = Definition.objects.create(owner_id=member.user_id,installation_id=member.installation_id,
            entity_id=entity_id,name=name,revision=1)
    else:
        if row.revision!=expected_revision: raise Conflict()
        require(row.revision<200)
        if (str(row.entity_id) if row.entity_id else None) != entity_id: raise Unavailable()
        row.revision += 1; row.name = name
        row.save(update_fields=('revision','name'))
    DefinitionVersion.objects.create(definition=row,revision=row.revision,schema=schema,schema_sha256=digest,runtime=RUNTIME)
    return _definition_value(row)


@transaction.atomic
def get_definition(user,definition_id):
    return _definition_value(_definition(_principal(user),definition_id))


@transaction.atomic
def dispatch(user,definition_id,revision,seed,dispatch_key):
    member = _principal(user); definition = _definition(member,definition_id); revision=_revision(revision)
    require(type(seed) is dict)
    seed,digest = bounded_json(seed)
    require(type(dispatch_key) is str and 0<len(dispatch_key)<=128 and dispatch_key==dispatch_key.strip()
            and not any(ord(c)<32 or ord(c)>=127 for c in dispatch_key))
    version = definition.versions.filter(revision=revision).first()
    if version is None: raise Conflict()
    old = WorkflowRun.objects.select_related('version').filter(definition=definition,dispatch_key=dispatch_key).first()
    if old is not None:
        if old.version_id!=version.pk or old.seed_sha256!=digest or old.seed!=seed: raise Conflict()
        return _run_value(old)
    require(WorkflowRun.objects.filter(definition=definition).count()<1000)
    _,graph_digest,_=validate(version.schema)
    if version.runtime != RUNTIME or version.schema_sha256!=graph_digest: raise Unavailable()
    run = WorkflowRun.objects.create(definition=definition,version=version,dispatch_key=dispatch_key,seed=seed,
        seed_sha256=digest,access_revision=member.installation.access_revision)
    return _run_value(run)


@transaction.atomic
def get_run(user,run_id):
    return _run_value(_run(_principal(user),run_id))


def _coordinator(run):
    _,digest,graph=validate(run.version.schema)
    _,seed_digest=bounded_json(run.seed)
    if run.version.runtime!=RUNTIME or run.version.schema_sha256!=digest or run.seed_sha256!=seed_digest:
        raise Unavailable()
    executors=ExecutorRegistry(kinds=REGISTRY)
    # Bind only real, supported deterministic package executors. Do not use
    # builtin.executors(), whose unrelated defaults include offline fake clients.
    executors.bind('manual_trigger',trigger.manual_trigger).bind('transform',logic.transform).bind('output',output.output)
    start = next(node.id for node in graph.nodes if node.type.rsplit('/',1)[-1]=='manual_trigger')
    return Coordinator(graph=graph,executors=executors,run=str(run.pk),store=DjangoClaimStore(run),
                       initial_inputs={start:run.seed},kinds=REGISTRY)


@transaction.atomic
def advance(user,run_id,expected_step):
    run = _run(_principal(user),run_id)
    if run.step!=_revision(expected_step): raise Conflict()
    if run.status in ('completed','failed','cancelled'): return _run_value(run)
    coordinator = _coordinator(run)
    run.status = 'running'
    try:
        # A savepoint permits a bounded-output refusal to roll back its claim
        # while recording a safe run-level diagnostic in the outer transaction.
        with transaction.atomic():
            ready = coordinator.advance()
            if ready:
                outcome = coordinator.run_node(ready[0],owner=uuid.uuid4().hex)
                if outcome.status=='failed':
                    run.status,run.error='failed','execution_failed'
            coordinator.advance()
            state=coordinator.store.state(str(run.pk))
            if Frontier.is_complete(coordinator.graph,state):
                run.status='failed' if any(row.status=='failed' for row in state.values()) else 'completed'
            elif not ready:
                run.status,run.error='failed','run_blocked'
    except OutputLimit:
        # The failed savepoint left no partial output. Record the exact failed
        # node and safe reason so triage can name the step that needs repair.
        store=DjangoClaimStore(run)
        if not store.claim(str(run.pk),ready[0],uuid.uuid4().hex): raise Unavailable()
        store.fail(str(run.pk),ready[0],'output_limit')
        run.status,run.error='failed','output_limit'
    run.step += 1
    run.save(update_fields=('status','error','step'))
    return _run_value(run)


@transaction.atomic
def cancel(user,run_id,expected_step):
    run = _run(_principal(user),run_id)
    if run.step!=_revision(expected_step): raise Conflict()
    if run.status not in ('completed','failed','cancelled'):
        run.status='cancelled'; run.step+=1
        run.save(update_fields=('status','step'))
    return _run_value(run)
