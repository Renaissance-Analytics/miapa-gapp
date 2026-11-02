"""Original-host authoring over the public Flow bridge and shared draft services.

No executable runtime, graph grant or human answer is provisioned here.
"""
import asyncio
from copy import deepcopy
import json,os,shutil,subprocess,time,uuid
from pathlib import Path
from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.db import connections,transaction,DatabaseError
from django.db.models import F
from backend.maipa.workflows import drafts,services,native
from backend.maipa.workflows.models import NativePrincipalBinding,NativeRunAuthority
from backend.maipa.workflows.errors import Unavailable,Conflict,InvalidWorkflow
from backend.maipa.workflows.schema import bounded_json,handle,KINDS
from backend.maipa.intake.models import Entity
from . import ui
from .models import UiComposition

TOOLS={
 'flow_get_state':(),'flow_list_nodes':(),'flow_get_node':('id',),'flow_list_edges':(),'flow_get_node_schema':('name',),
 'flow_add_node':('kind','label','description','config','x','y'),
 'flow_update_node':('id','label','description','config','x','y'),'flow_delete_node':('id',),
 'flow_connect':('source','target','sourceHandle','targetHandle','label'),'flow_disconnect':('id',),
 'maipa_workflow_list':(),'maipa_workflow_create':('name','entity_id','request_id'),'maipa_workflow_publish':('expected_revision',),
 'flow_run':(),'flow_cancel':('runId',),'maipa_workflow_runs':()}
READS={'flow_get_state','flow_list_nodes','flow_get_node','flow_list_edges'}
MUTATIONS={'flow_add_node','flow_update_node','flow_delete_node','flow_connect','flow_disconnect'}
ALLOWED_KINDS={'@particle-academy/'+kind for kind in KINDS}
_definitions=None

def _error():return {'content':[{'type':'text','text':'The workflow changed or is unavailable. Read its current graph before editing.'}],'isError':True}
def _result(value):return {'content':[{'type':'text','text':json.dumps(value,ensure_ascii=True)}],'structuredContent':value}

@sync_to_async(thread_sensitive=True)
def _database(operation,*args):
 try:return operation(*args)
 finally:
  for connection in connections.all():
   if not connection.in_atomic_block:connection.close()

async def _run(graph,revision,name=None,arguments=None,commit=None,control=None):
 root=Path(__file__).resolve().parents[3]/'frontend';node=settings.ACP_NODE_EXECUTABLE or shutil.which('node')
 if not node or not Path(node).is_absolute():raise Unavailable()
 environment={key:value for key,value in os.environ.items() if key.upper() in {'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','TEMP','TMP'}}
 process=None
 try:
  process=await asyncio.create_subprocess_exec(node,str(root/'tools/flow-host.mjs'),cwd=root,env=environment,
   stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,
   limit=131072,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
  payload={'graph':graph,'revision':revision,'listOnly':name is None,'name':name,'arguments':arguments}
  wire=json.dumps(payload,allow_nan=False,ensure_ascii=True).encode()+b'\n'
  if len(wire)>65536:raise Unavailable()
  process.stdin.write(wire);await asyncio.wait_for(process.stdin.drain(),5)
  async with asyncio.timeout(12):
   for _ in range(3):
    line=await process.stdout.readline()
    if not line or len(line)>65536:raise Unavailable()
    value=json.loads(line)
    if type(value) is not dict:raise Unavailable()
    if set(value)=={'kind','revision','graph'} and value['kind']=='commit' and commit:
     answer=await commit(value['revision'],value['graph'])
     process.stdin.write(json.dumps(answer,allow_nan=False).encode()+b'\n');await process.stdin.drain()
    elif set(value)=={'kind','tool','revision','runId'} and value['kind']=='control' and control:
     answer=await control(value['tool'],value['revision'],value['runId'])
     process.stdin.write(json.dumps(answer,allow_nan=False).encode()+b'\n');await process.stdin.drain()
    elif set(value)=={'kind','result'} and value['kind']=='result':
     await process.wait()
     if process.returncode!=0:raise Unavailable()
     return value['result']
    else:raise Unavailable()
   raise Unavailable()
 except (OSError,ValueError,TypeError,TimeoutError,RecursionError):raise Unavailable() from None
 finally:
  if process is not None and process.returncode is None:
   process.kill();await process.wait()

async def public_workflow_tools():
 global _definitions
 if _definitions is None:
  value=await _run({'nodes':[],'edges':[]},0)
  if type(value) is not list or {tool.get('name') for tool in value}!=set(TOOLS)-{'maipa_workflow_list','maipa_workflow_create','maipa_workflow_publish','maipa_workflow_runs'}:raise Unavailable()
  own=[]
  for name,description,properties in (
   ('maipa_workflow_list','Read your owned workflow drafts, available client entity IDs/names, current visible draft and admitted kinds. Use an available entity_id when creating a client workflow. No execution authority.',{}),
   ('maipa_workflow_create','Create an empty owned authoring draft. Reuse request_id for retries. Present Maipa.Workflow with its draft_id to edit it. Creates no run or authority.',
    {'name':{'type':'string','minLength':1,'maxLength':128},'entity_id':{'type':['string','null'],'format':'uuid'},'request_id':{'type':'string','minLength':1,'maxLength':80}}),
   ('maipa_workflow_publish','Publish the visible, observed draft as one validated immutable version. Creates no run or execution grant.',
    {'expected_revision':{'type':'integer','minimum':0,'maximum':1999999998}}),
   ('maipa_workflow_runs','Inspect saved actual runs of the visible workflow under the original registered principal and current triage grant. Returns no authority or human answers.',{})):
   own.append({'name':name,'description':description,'inputSchema':{'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}})
  _definitions=value+own
 return deepcopy(_definitions)

class WorkflowBroker:
 def __init__(self,actor,account_id,identity):
  if not isinstance(actor,get_user_model()) or actor.is_authenticated is not True or actor.pk is None:raise Unavailable()
  self.actor,self.account_id,self.identity=actor,services.identity(account_id),services.identity(identity)
  self.observed=None;self.lock=asyncio.Lock();self.last_activity=None
  self.observed_runs={}

 def _actor(self):return drafts.native_actor(self.actor,self.account_id,self.identity)

 @transaction.atomic
 def _current(self):
  # Match the UI service's row lock order; a closed/replaced component cannot
  # race the authoring transaction and silently select a different draft.
  UiComposition.objects.filter(pk=self.identity,session__user_id=self.actor.pk,session__account_id=self.account_id).update(revision=F('revision'))
  context=self._actor();composition=ui.ui_snapshot(self.actor,self.account_id,self.identity)['composition'];found=[]
  def visit(node):
   if node['type']=='Maipa.Workflow':found.append((node['id'],node['props']['draft_id']))
   for child in node.get('children',[]):visit(child)
  if composition:visit(composition['schema'])
  if len(found)!=1:raise Unavailable()
  component,identity=found[0];return {'component':component,'draft':drafts.get(context,identity)}

 @transaction.atomic
 def _metadata(self):
  context=self._actor();rows=drafts.list_drafts(context);_,member=drafts._principal(context)
  entities=[{'id':str(row.pk),'name':row.name,'client_name':row.engagement.name} for row in Entity.objects.select_related('engagement').filter(
   engagement__owner_id=member.user_id,engagement__installation_id=member.installation_id).order_by('name','id')[:100]]
  try:current=self._current()['draft'];visible={'id':current['id'],'revision':current['revision']}
  except Unavailable:visible=None
  self._actor()
  return {'drafts':[{key:row[key] for key in ('id','name','entity_id','revision')} for row in rows],
   'current':visible,'kinds':sorted(ALLOWED_KINDS),'entities':entities}

 @transaction.atomic
 def _create(self,args):
  handle(args['request_id']);context=self._actor()
  key=str(uuid.uuid5(uuid.UUID(self.identity),args['request_id']))
  row=drafts.create(context,args['name'],key,entity_id=args['entity_id'])
  return {key:row[key] for key in ('id','name','entity_id','revision')}

 @transaction.atomic
 def _commit(self,original,revision,graph):
  current=self._current()
  if (current['component'],current['draft']['id'])!=(original['component'],original['draft']['id']):raise Conflict()
  return drafts.change(self._actor(),original['draft']['id'],revision,graph)

 @transaction.atomic
 def _publish(self,original,revision):
  current=self._current()
  if (current['component'],current['draft']['id'])!=(original['component'],original['draft']['id']):raise Conflict()
  if revision!=original['draft']['revision'] or current['draft']['revision']!=revision:raise Conflict()
  return drafts.publish(self._actor(),original['draft']['id'],revision)

 def _execution_actor(self):
  rows=list(NativePrincipalBinding.objects.filter(owner_id=self.actor.pk,account_id=self.account_id,ui_id=self.identity)[:2])
  if len(rows)!=1:raise Unavailable()
  return native.actor(self.actor,str(rows[0].pk)),rows[0]

 @transaction.atomic
 def _runs(self):
  current=self._current();context,binding=self._execution_actor();publication=current['draft']['publication']
  if not publication:return {'runs':[]}
  rows=NativeRunAuthority.objects.filter(binding=binding,run__definition_id=publication['definition_id']).select_related('run').order_by('-run__created_at','-run_id')[:10]
  runs=[native.get_run(context,str(row.run_id)) for row in rows]
  self.observed_runs={r['id']:r for r in runs}
  return {'runs':runs}

 @transaction.atomic
 def _control(self,original,tool,revision,run_id):
  current=self._current()
  if (current['component'],current['draft']['id'],current['draft']['revision'])!=(original['component'],original['draft']['id'],revision):raise Conflict()
  context,binding=self._execution_actor()
  publication=current['draft']['publication']
  if tool=='flow_run':
   if not publication or publication['draft_revision']!=revision:raise Conflict()
   native.prepare_graph(self.actor,publication['definition_id'],publication['definition_revision'])
   key=str(uuid.uuid5(uuid.UUID(self.identity),'flow-run:'+current['draft']['id']+':'+str(revision)))
   native.dispatch(context,publication['definition_id'],publication['definition_revision'],{},key)
  elif tool=='flow_cancel':
   if run_id is None:
    if len(self.observed_runs)!=1:raise Unavailable()
    run_id=next(iter(self.observed_runs))
   if run_id not in self.observed_runs:raise Unavailable()
   observed=self.observed_runs[run_id]
   if not publication or observed['definition_id']!=publication['definition_id']:raise Unavailable()
   native.cancel(context,run_id,observed['step'])
  else:raise Unavailable()
  return {'ok':True}

 async def tools(self):return await public_workflow_tools()

 async def call(self,name,arguments):
  async with self.lock:
   self.last_activity=None
   try:
    if name not in TOOLS or type(arguments) is not dict or set(arguments)-set(TOOLS[name]):return _error()
    arguments,_=bounded_json(arguments,12000)
    definitions=await public_workflow_tools();definition=next(row for row in definitions if row['name']==name)
    if any(key not in arguments for key in definition['inputSchema'].get('required',[])):return _error()
    for key,value in arguments.items():
     if key in ('x','y'):
      if type(value) not in (int,float) or abs(value)>1000000:return _error()
     elif key=='config':
      if type(value) is not dict:return _error()
     elif key=='expected_revision':services._revision(value)
     elif key=='entity_id' and value is None:pass
     elif type(value) is not str:return _error()
    if name=='flow_add_node' and arguments.get('kind') not in ALLOWED_KINDS:return _error()
    if name=='flow_get_node_schema' and arguments.get('name') not in ALLOWED_KINDS:return _error()
    if name=='maipa_workflow_list':return _result(await _database(self._metadata))
    if name=='maipa_workflow_create':return _result(await _database(self._create,arguments))
    if name=='maipa_workflow_runs':return _result(await _database(self._runs))
    current=await _database(self._current)
    if name in MUTATIONS or name in ('maipa_workflow_publish','flow_run','flow_cancel'):
     if not self.observed or (self.observed['component'],self.observed['draft']['id'])!=(current['component'],current['draft']['id']):return _error()
     state=deepcopy(self.observed)
    else:state=current
    if name=='maipa_workflow_publish':return _result(await _database(self._publish,state,arguments['expected_revision']))
    accepted=None
    async def commit(revision,graph):
     nonlocal accepted
     try:
      row=await _database(self._commit,state,revision,graph)
      if row['revision']>state['draft']['revision']:
       accepted={'id':str(uuid.uuid4()),'ui_id':self.identity,'draft_id':row['id'],'component_id':state['component'],
        'revision':row['revision'],'action':name,'at_ms':int(time.time()*1000)}
      self.observed={'component':state['component'],'draft':deepcopy(row)}
      return {'ok':True,'graph':row['graph'],'revision':row['revision']}
     except (Unavailable,Conflict,InvalidWorkflow,ui.UiUnavailable):return {'ok':False}
    async def control(tool,revision,run_id):
     try:
      if tool!=name or revision!=state['draft']['revision']:return {'ok':False}
      return await _database(self._control,state,tool,revision,run_id)
     except (Unavailable,Conflict,InvalidWorkflow,ui.UiUnavailable):return {'ok':False}
    result=await _run(state['draft']['graph'],state['draft']['revision'],name,arguments,commit,control)
    checked=await _database(self._current)
    if (checked['component'],checked['draft']['id'])!=(state['component'],state['draft']['id']):return _error()
    if type(result) is not dict or set(result)-{'content','structuredContent','isError'} or type(result.get('content')) is not list:return _error()
    if any(type(item) is not dict or set(item)!={'type','text'} or item['type']!='text' or type(item['text']) is not str for item in result['content']):return _error()
    if not result.get('isError',False):
     if name in READS:self.observed=deepcopy(current)
     if accepted:self.last_activity=accepted
    return result
   except (Unavailable,Conflict,InvalidWorkflow,ui.UiUnavailable,DatabaseError,ObjectDoesNotExist,ValueError,TypeError,OSError,TimeoutError):return _error()
