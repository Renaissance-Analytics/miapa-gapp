"""Owned shared authoring over Fancy graph types, separate from executable runs.

The native authoring context carries only the original UI lease. It never
creates an AgentPrincipal, execution grant, human response or workflow run.
"""
from dataclasses import dataclass
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from fancy_flow import FlowGraph,FlowNode,FlowEdge,export_workflow
from backend.maipa.agent_accounts import ui
from . import services
from .models import WorkflowDraft
from .errors import Conflict,InvalidWorkflow,Unavailable
from .schema import bounded_json,require,handle,REGISTRY,KINDS

_SEAL=object()


@dataclass(frozen=True)
class _NativeAuthor:
    user_id:int
    account_id:str
    ui_id:str
    seal:object


def _principal(context):
    if type(context) is _NativeAuthor and context.seal is _SEAL:
        user=get_user_model().objects.filter(pk=context.user_id,is_active=True).first()
        member=services._principal(user)
        try:ui._lookup(user,context.account_id,context.ui_id)
        except (ValueError,OSError,ui.UiUnavailable,ObjectDoesNotExist):raise Unavailable() from None
        return user,member
    if not isinstance(context,get_user_model()):raise Unavailable()
    return context,services._principal(context)


@transaction.atomic
def native_actor(user,account_id,ui_id):
    member=services._principal(user)
    context=_NativeAuthor(member.user_id,services.identity(account_id),services.identity(ui_id),_SEAL)
    _principal(context)
    return context


def validate_graph(value):
    """Authoring admission permits incomplete config/wiring; execution does not."""
    value,digest=bounded_json(value,49152)
    require(type(value) is dict and set(value)=={'nodes','edges'})
    require(type(value['nodes']) is list and len(value['nodes'])<=30 and type(value['edges']) is list and len(value['edges'])<=60)
    ids=set()
    for node in value['nodes']:
        require(type(node) is dict and set(node)=={'id','type','position','data'})
        handle(node['id']);require(node['id'] not in ids);ids.add(node['id'])
        position=node['position'];require(type(position) is dict and set(position)=={'x','y'})
        require(all(type(n) in (int,float) and abs(n)<=1_000_000 for n in position.values()))
        data=node['data'];require(type(data) is dict and {'kind','label','config'}<=set(data)<={'kind','label','config','description','startingMsg','stoppingMsg','status'})
        require(type(data['kind']) is str and node['type']==data['kind'])
        kind=REGISTRY.get(data['kind']);require(kind is not None and kind.name.rsplit('/',1)[-1] in KINDS)
        for key in ('label','description','startingMsg','stoppingMsg'):
            if key in data:require(type(data[key]) is str and len(data[key])<=1024)
        require('status' not in data or data['status']=='idle')
        config=data['config'];require(type(config) is dict and set(config)<={field.key for field in kind.config_schema})
        if kind.name.rsplit('/',1)[-1]=='human_approval':require(set(config)<={'title','description'})
        if kind.name.rsplit('/',1)[-1]=='user_input':require(set(config)<={'title','fields'})
    edges=set()
    for edge in value['edges']:
        require(type(edge) is dict and {'id','source','target'}<=set(edge)<={'id','source','target','sourceHandle','targetHandle','label'})
        handle(edge['id']);require(edge['id'] not in edges);edges.add(edge['id'])
        require(type(edge['source']) is str and type(edge['target']) is str and edge['source'] in ids and edge['target'] in ids)
        for key in ('sourceHandle','targetHandle'):
            if edge.get(key) is not None:handle(edge[key])
        if 'label' in edge:require(type(edge['label']) is str and len(edge['label'])<=1024)
    return value,digest


def _draft(member,identity):
    row=WorkflowDraft.objects.select_related('definition').filter(pk=services.identity(identity),owner_id=member.user_id,
        installation_id=member.installation_id).first()
    if row is None:raise Unavailable()
    services._entity(member,str(row.entity_id) if row.entity_id else None)
    try:_,digest=validate_graph(row.graph)
    except InvalidWorkflow:raise Unavailable() from None
    if digest!=row.graph_sha256:raise Unavailable()
    return row


def _receipt(row):
    if row.definition_id is None:
        if row.published_draft_revision is not None or row.published_definition_revision is not None:raise Unavailable()
        return None
    definition=row.definition
    if (definition.owner_id,definition.installation_id,definition.entity_id)!=(row.owner_id,row.installation_id,row.entity_id):raise Unavailable()
    version=definition.versions.filter(revision=row.published_definition_revision).first()
    if version is None or row.published_draft_revision is None or row.published_draft_revision>row.revision:raise Unavailable()
    return {'status':'published','draft_id':str(row.pk),'draft_revision':row.published_draft_revision,
        'definition_id':str(definition.pk),'definition_revision':version.revision,'schema_sha256':version.schema_sha256}


def _value(row):
    return {'id':str(row.pk),'owner_id':str(row.owner_id),'name':row.name,'entity_id':str(row.entity_id) if row.entity_id else None,
        'revision':row.revision,'graph':row.graph,'graph_sha256':row.graph_sha256,'publication':_receipt(row)}


@transaction.atomic
def create(context,name,request_key,*,entity_id=None):
    _,member=_principal(context);request_key=services.identity(request_key)
    require(type(name) is str and name==name.strip() and 0<len(name)<=128 and not any(ord(c)<32 or ord(c)==127 for c in name))
    _,digest=bounded_json({'name':name,'entity_id':entity_id})
    if entity_id is not None:entity_id=services.identity(entity_id)
    services._entity(member,entity_id)
    old=WorkflowDraft.objects.filter(owner_id=member.user_id,installation_id=member.installation_id,create_key=request_key).first()
    if old is not None:
        if old.create_sha256!=digest:raise Conflict()
        return _value(_draft(member,str(old.pk)))
    require(WorkflowDraft.objects.filter(owner_id=member.user_id,installation_id=member.installation_id).count()<100)
    graph,graph_hash=validate_graph({'nodes':[],'edges':[]})
    row=WorkflowDraft.objects.create(owner_id=member.user_id,installation_id=member.installation_id,entity_id=entity_id,
        name=name,create_key=request_key,create_sha256=digest,graph=graph,graph_sha256=graph_hash)
    return _value(row)


@transaction.atomic
def get(context,identity):
    _,member=_principal(context)
    return _value(_draft(member,identity))


@transaction.atomic
def list_drafts(context):
    _,member=_principal(context)
    # Each row is revalidated; damaged or transferred state is never repaired.
    return [_value(_draft(member,str(row.pk))) for row in WorkflowDraft.objects.filter(owner_id=member.user_id,
        installation_id=member.installation_id).order_by('created_at','id')[:100]]


@transaction.atomic
def change(context,identity,expected_revision,graph):
    graph,digest=validate_graph(graph)
    _,member=_principal(context);row=_draft(member,identity)
    if row.revision!=services._revision(expected_revision):raise Conflict()
    if digest!=row.graph_sha256:
        require(row.revision<1_999_999_998)
        row.revision+=1;row.graph,row.graph_sha256=graph,digest
        row.save(update_fields=('revision','graph','graph_sha256'))
    return _value(row)


def _export(value):
    # Thin shape adapter to the PUBLIC Fancy exporter, not a graph runner.
    nodes=[]
    for node in value['nodes']:
        data=node['data']
        nodes.append(FlowNode(id=node['id'],type=data['kind'],x=node['position']['x'],y=node['position']['y'],
            label=data['label'],config=data['config'],description=data.get('description'),
            starting_msg=data.get('startingMsg'),stopping_msg=data.get('stoppingMsg')))
    edges=[FlowEdge(id=e['id'],source=e['source'],target=e['target'],source_handle=e.get('sourceHandle'),
        target_handle=e.get('targetHandle'),label=e.get('label')) for e in value['edges']]
    return export_workflow(FlowGraph(nodes=tuple(nodes),edges=tuple(edges)))


@transaction.atomic
def publish(context,identity,expected_revision):
    user,member=_principal(context);row=_draft(member,identity)
    if row.revision!=services._revision(expected_revision):raise Conflict()
    previous=_receipt(row)
    if previous and previous['draft_revision']==row.revision:return previous
    if row.definition_id and row.definition.revision!=row.published_definition_revision:raise Conflict()
    definition=services.save_definition(user,row.name,_export(row.graph),entity_id=str(row.entity_id) if row.entity_id else None,
        definition_id=str(row.definition_id) if row.definition_id else None,expected_revision=row.published_definition_revision or 0)
    row.definition_id=definition['id'];row.published_definition_revision=definition['revision'];row.published_draft_revision=row.revision
    row.save(update_fields=('definition','published_definition_revision','published_draft_revision'))
    row.refresh_from_db()
    return _receipt(row)
