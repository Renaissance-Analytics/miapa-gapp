"""Authenticated human records around Fancy's actual durable gate executors.

No agent tool calls this response factory. The HTTP adapter derives its human
from the actual session; no submitted actor, seed or graph value is authority.
These nonfinancial gates never authorize protected business effects.
"""
from django.db import transaction
from fancy_flow.durable.human import Submissions,DurableApproval,DurableUserInput
from fancy_flow.runtime.pause import Pause

from . import services
from .errors import Conflict,Unavailable
from .models import HumanGate,HumanResponse,NodeCheckpoint
from .schema import RUNTIME,bounded_json,gate_config,require,validate


def _node(run,node_id):
    node=next((n for n in run.version.schema['graph']['nodes'] if n['id']==node_id),None)
    if node is None or node['kind'].rsplit('/',1)[-1] not in ('human_approval','user_input'):raise Unavailable()
    return node


def _request_hash(run,node_id,kind,request,input_hash,step):
    return bounded_json({'run':str(run.pk),'version':run.version.revision,'schema':run.version.schema_sha256,
        'seed':run.seed_sha256,'node':node_id,'kind':kind,'request':request,'input':input_hash,'step':step},65536)[1]


def _verify(gate,run):
    _,digest,_=validate(run.version.schema);_,seed_hash=bounded_json(run.seed)
    if run.version.runtime!=RUNTIME or digest!=run.version.schema_sha256 or seed_hash!=run.seed_sha256:raise Unavailable()
    node=_node(run,gate.node_id);kind=node['kind'].rsplit('/',1)[-1]
    _,input_hash=bounded_json(gate.observed_input)
    if (gate.run_id!=run.pk or gate.kind!=kind or gate.request!=gate_config(kind,node['config'])
            or gate.input_sha256!=input_hash or gate.request_sha256!=_request_hash(run,gate.node_id,kind,gate.request,input_hash,gate.parked_step)):
        raise Unavailable()


def _answer(gate,value):
    value,digest=bounded_json(value)
    if gate.kind=='human_approval':require(type(value) is bool)
    else:
        fields=gate.request['fields'];require(type(value) is dict and set(value)=={f['key'] for f in fields})
        for field in fields:
            item=value[field['key']]
            if field['type']=='checkbox':require(type(item) is bool)
            else:
                require(type(item) is str and len(item)<=2000)
                require(not field.get('required',False) or bool(item.strip()))
    return value,digest


def park(run,node_id,reason,observed):
    signal=Pause.decode(reason)
    node=_node(run,node_id);kind=node['kind'].rsplit('/',1)[-1]
    request=gate_config(kind,node['config']);expected='approval' if kind=='human_approval' else 'input'
    if signal is None or signal.node_id!=node_id or signal.awaiting!=expected or signal.detail!=request or type(observed) is not dict:
        raise Unavailable()
    observed,input_hash=bounded_json(observed)
    if HumanGate.objects.filter(run=run,node_id=node_id).exists():raise Unavailable()
    step=run.step+1
    HumanGate.objects.create(run=run,node_id=node_id,kind=kind,request=request,observed_input=observed,
        input_sha256=input_hash,parked_step=step,request_sha256=_request_hash(run,node_id,kind,request,input_hash,step))


def _response_value(response):
    gate=response.gate
    return {'status':'recorded','id':str(response.pk),'run_id':str(gate.run_id),'gate_id':str(gate.pk),
        'accepted_step':gate.parked_step,'next_step':gate.parked_step+1}


def _current_run(user,run_id):
    run=services._run(services._principal(user),run_id)
    if not services._origin_matches(run):raise Unavailable()
    return run


@transaction.atomic
def get_pending(user,run_id):
    run=_current_run(user,run_id)
    if run.status!='waiting':return None
    rows=list(HumanGate.objects.filter(run=run,response__isnull=True)[:2])
    if len(rows)!=1:raise Unavailable()
    gate=rows[0];_verify(gate,run)
    if gate.parked_step!=run.step or not NodeCheckpoint.objects.filter(run=run,node_id=gate.node_id,status='paused').exists():raise Unavailable()
    return {'id':str(gate.pk),'run_id':str(run.pk),'node_id':gate.node_id,'kind':gate.kind,'step':run.step,
        'definition_revision':run.version.revision,'schema_sha256':run.version.schema_sha256,
        'request_sha256':gate.request_sha256,'request':gate.request,'input':gate.observed_input}


@transaction.atomic
def respond(user,run_id,gate_id,*,expected_step,definition_revision,request_sha256,request_key,answer):
    run=_current_run(user,run_id)
    gate=HumanGate.objects.filter(pk=services.identity(gate_id),run=run).first()
    if gate is None:raise Unavailable()
    _verify(gate,run);services._revision(expected_step);services._revision(definition_revision)
    if (expected_step,definition_revision,request_sha256)!=(gate.parked_step,run.version.revision,gate.request_sha256):raise Conflict()
    request_key=services.identity(request_key);answer,digest=_answer(gate,answer)
    old=HumanResponse.objects.filter(gate=gate).first()
    if old is not None:
        if old.human_id!=user.pk or str(old.request_key)!=request_key or old.answer!=answer or old.answer_sha256!=digest:raise Conflict()
        return _response_value(old)
    if run.status!='waiting' or run.step!=expected_step:raise Conflict()
    if run.origin_kind=='native':
        from .native import guard_run
        if not guard_run(run):raise Unavailable()
    # Exact paused claim only: never erase live/settled work, steal by age/PID,
    # or discard the original first-attempt clock/history.
    changed=NodeCheckpoint.objects.filter(run=run,node_id=gate.node_id,status='paused').update(status='released',owner=None,error=None)
    if changed!=1:raise Conflict()
    response=HumanResponse.objects.create(gate=gate,human=user,request_key=request_key,answer=answer,answer_sha256=digest)
    run.status,run.error='queued',None;run.step+=1;run.save(update_fields=('status','error','step'))
    return _response_value(response)


def released_answer(run,checkpoint):
    response=HumanResponse.objects.select_related('gate').filter(gate__run=run,gate__node_id=checkpoint.node_id).first()
    if response is None:raise Unavailable()
    _verify(response.gate,run);value,digest=_answer(response.gate,response.answer)
    if digest!=response.answer_sha256 or response.human_id!=run.origin_owner_id or run.step<response.gate.parked_step+1:raise Unavailable()
    return value


class _ObservedGate:
    def __init__(self,run,store,executor):self.run,self.store,self.executor=run,store,executor

    def execute(self,context):
        observed,digest=bounded_json(context.inputs)
        gate=HumanGate.objects.filter(run=self.run,node_id=context.node.id).first()
        if gate is not None:
            _verify(gate,self.run)
            if gate.input_sha256!=digest:raise Unavailable()
        self.store.observed[context.node.id]=observed
        return self.executor.execute(context)


def executors_for(run,store):
    submissions=Submissions()
    for response in HumanResponse.objects.select_related('gate').filter(gate__run=run).order_by('gate__parked_step'):
        _verify(response.gate,run);value,digest=_answer(response.gate,response.answer)
        if digest!=response.answer_sha256 or response.human_id!=run.origin_owner_id:raise Unavailable()
        submissions.park(response.gate.node_id);submissions.record(response.gate.node_id,value)
    return {'human_approval':_ObservedGate(run,store,DurableApproval(submissions)),
            'user_input':_ObservedGate(run,store,DurableUserInput(submissions))}
