import type {FlowGraph} from '@particle-academy/fancy-flow';
import {csrfToken} from './api.js';

export interface Publication {status:'published';draft_id:string;draft_revision:number;definition_id:string;definition_revision:number;schema_sha256:string}
export interface WorkflowDraft {id:string;owner_id:string;name:string;entity_id:string|null;revision:number;graph:FlowGraph;graph_sha256:string;publication:Publication|null}
export interface WorkflowState {phase:'loading'|'ready'|'saving'|'publishing'|'conflict'|'unavailable'|'closed';saved:WorkflowDraft|null;graph:FlowGraph|null}
export interface WorkflowActivity {id:string;ui_id:string;draft_id:string;component_id:string;revision:number;action:'flow_add_node'|'flow_update_node'|'flow_delete_node'|'flow_connect'|'flow_disconnect';at_ms:number}
export class WorkflowError extends Error {constructor(readonly kind:'conflict'|'closed'|'unavailable'|'invalid'){super('Workflow unavailable');}}
type Request=(action:'read'|'write'|'publish',command?:Record<string,unknown>,signal?:AbortSignal)=>Promise<unknown>;
const object=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v);
const exact=(v:Record<string,unknown>,keys:string[])=>Object.keys(v).length===keys.length&&keys.every(k=>Object.hasOwn(v,k));
const uuid=(v:unknown):v is string=>typeof v==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(v);
const count=(v:unknown):v is number=>Number.isSafeInteger(v)&&Number(v)>=0&&Number(v)<2_000_000_000;
const handle=(v:unknown):v is string=>typeof v==='string'&&/^[A-Za-z0-9_-]{1,80}$/.test(v);
function fail():never {throw new WorkflowError('unavailable');}
const kinds=new Set(['manual_trigger','transform','output','human_approval','user_input'].flatMap(k=>[k,'@particle-academy/'+k]));
function bounded(v:unknown,depth=0):void {
 if(depth>12)fail();
 if(typeof v==='number'){if(!Number.isFinite(v)||!Number.isSafeInteger(v)&&Number.isInteger(v))fail();}
 else if(typeof v==='string'){if(v.length>49152||/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/.test(v))fail();}
 else if(Array.isArray(v)){for(const child of v)bounded(child,depth+1);}
 else if(object(v)){for(const [key,child] of Object.entries(v)){bounded(key,depth+1);bounded(child,depth+1);}}
 else if(v!==null&&typeof v!=='boolean')fail();
}

/** Persistent graph shape. Canvas selection, drag and measured size are transient. */
export function editorGraph(input:unknown):FlowGraph {
 bounded(input);if(!object(input)||!exact(input,['nodes','edges'])||!Array.isArray(input.nodes)||input.nodes.length>30||!Array.isArray(input.edges)||input.edges.length>60)fail();
 const ids=new Set<string>(),edgeIds=new Set<string>();
 const nodes=input.nodes.map(value=>{
  if(!object(value)||!handle(value.id)||ids.has(value.id)||typeof value.type!=='string'||!kinds.has(value.type)
   ||Object.keys(value).some(k=>!['id','type','position','data','selected','dragging','measured','resizing','width','height'].includes(k))
   ||!object(value.position)||!exact(value.position,['x','y'])||Object.values(value.position).some(n=>typeof n!=='number'||!Number.isFinite(n)||Math.abs(n)>1e6)
   ||!object(value.data)||value.data.kind!==value.type||typeof value.data.label!=='string'||value.data.label.length>1024||!object(value.data.config)
   ||Object.keys(value.data).some(k=>!['kind','label','config','description','startingMsg','stoppingMsg','status'].includes(k))
   ||value.data.status!==undefined&&value.data.status!=='idle')return fail();
  for(const key of ['description','startingMsg','stoppingMsg'])if(value.data[key]!==undefined&&(typeof value.data[key]!=='string'||String(value.data[key]).length>1024))fail();
  for(const key of ['selected','dragging','resizing'])if(value[key]!==undefined&&typeof value[key]!=='boolean')fail();
  if(value.measured!==undefined&&(!object(value.measured)||Object.keys(value.measured).some(k=>!['width','height'].includes(k))
   ||Object.values(value.measured).some(n=>typeof n!=='number'||!Number.isFinite(n)||n<=0||n>10000)))fail();
  const dimensions:Record<string,number>={};
  for(const key of ['width','height'])if(value[key]!==undefined){const n=value[key];if(typeof n!=='number'||!Number.isFinite(n)||n<=0||n>10000)fail();dimensions[key]=n;}
  ids.add(value.id);
  return {id:value.id,type:value.type,position:{x:Number(value.position.x),y:Number(value.position.y)},data:structuredClone(value.data),...dimensions};
 });
 const edges=input.edges.map(value=>{
  if(!object(value)||!handle(value.id)||edgeIds.has(value.id)||typeof value.source!=='string'||typeof value.target!=='string'||!ids.has(value.source)||!ids.has(value.target)
   ||Object.keys(value).some(k=>!['id','source','target','sourceHandle','targetHandle','label','selected'].includes(k)))return fail();
  for(const key of ['sourceHandle','targetHandle'])if(value[key]!==undefined&&value[key]!==null&&!handle(value[key]))fail();
  if(value.label!==undefined&&(typeof value.label!=='string'||value.label.length>1024))fail();
  edgeIds.add(value.id);const {selected:_,...edge}=value;return structuredClone(edge);
 });
 const graph={nodes,edges};if(new TextEncoder().encode(JSON.stringify(graph)).length>49152)fail();return graph as FlowGraph;
}

export function parsePublication(v:unknown,id:string):Publication {
 if(!object(v)||!exact(v,['status','draft_id','draft_revision','definition_id','definition_revision','schema_sha256'])||v.status!=='published'||v.draft_id!==id
  ||!count(v.draft_revision)||!uuid(v.definition_id)||!count(v.definition_revision)||v.definition_revision<1||typeof v.schema_sha256!=='string'||!/^[a-f0-9]{64}$/.test(v.schema_sha256))return fail();
 return structuredClone(v) as unknown as Publication;
}
export function parseWorkflowActivity(v:unknown,ui:string):WorkflowActivity {
 if(!object(v)||!exact(v,['id','ui_id','draft_id','component_id','revision','action','at_ms'])||!uuid(v.id)||!uuid(v.ui_id)||v.ui_id!==ui||!uuid(v.draft_id)
  ||typeof v.component_id!=='string'||!/^[a-z][a-z0-9-]{0,47}$/.test(v.component_id)||!count(v.revision)
  ||!Number.isSafeInteger(v.at_ms)||Number(v.at_ms)<0||!['flow_add_node','flow_update_node','flow_delete_node','flow_connect','flow_disconnect'].includes(String(v.action)))return fail();
 return structuredClone(v) as unknown as WorkflowActivity;
}
export function parseWorkflowDraft(v:unknown,id:string,user:string):WorkflowDraft {
 if(!object(v)||!exact(v,['id','owner_id','name','entity_id','revision','graph','graph_sha256','publication'])||!uuid(id)||v.id!==id||v.owner_id!==user||!/^[0-9]+$/.test(user)
  ||typeof v.name!=='string'||!v.name.trim()||v.name.length>128||v.entity_id!==null&&!uuid(v.entity_id)||!count(v.revision)
  ||typeof v.graph_sha256!=='string'||!/^[a-f0-9]{64}$/.test(v.graph_sha256))return fail();
 const graph=editorGraph(v.graph),publication=v.publication===null?null:parsePublication(v.publication,id);
 if(publication&&publication.draft_revision>v.revision)fail();
 return {id,owner_id:user,name:v.name,entity_id:v.entity_id,revision:v.revision,graph,graph_sha256:v.graph_sha256,publication};
}

export function workflowRequest(user:string,id:string):Request {
 if(!uuid(id)||!/^[0-9]+$/.test(user))return fail();
 return async(action,command,signal)=>{
  const headers:Record<string,string>={'X-Maipa-Workflow-Actor':user};
  if(action!=='read'){headers['Content-Type']='application/json';headers['X-CSRFToken']=await csrfToken(fetch);}
  if(signal?.aborted)throw new WorkflowError('closed');
  const response=await fetch(`/api/workflows/drafts/${id}/${action==='publish'?'publish/':''}`,{
   method:action==='read'?'GET':'POST',credentials:'same-origin',cache:'no-store',redirect:'error',headers,
   signal:signal?AbortSignal.any([signal,AbortSignal.timeout(10000)]):AbortSignal.timeout(10000),...(command?{body:JSON.stringify(command)}:{})});
  if([401,403,404].includes(response.status))throw new WorkflowError('closed');
  if(response.status===409){const body:unknown=await response.json();throw new WorkflowError(object(body)&&object(body.error)&&body.error.code==='actor_changed'?'closed':'conflict');}
  if(response.status===400)throw new WorkflowError('invalid');
  if(response.status!==200)return fail();return response.json();
 };
}

/** Acknowledged shared graph state; a refused write never erases newer local work. */
export class SharedWorkflowController {
 state:WorkflowState={phase:'loading',saved:null,graph:null};
 private readonly request:Request;private readonly abort=new AbortController();
 private pending:FlowGraph|null=null;private writing:Promise<void>|null=null;private reading=false;private generation=0;
 constructor(readonly user:string,readonly id:string,readonly changed:(state:WorkflowState)=>void,request?:Request){this.request=request??workflowRequest(user,id);}
 private publish(state:WorkflowState){this.state=state;this.changed(state);}
 private failed(problem:unknown){
  if(this.state.phase==='closed')return;
  if(problem instanceof WorkflowError&&problem.kind==='closed'){this.dispose();return;}
  this.publish({...this.state,phase:problem instanceof WorkflowError&&problem.kind==='conflict'?'conflict':'unavailable'});
 }
 async load(explicit=false):Promise<void>{
  if(this.abort.signal.aborted||this.writing||this.reading||this.pending||this.state.phase==='publishing'||!explicit&&['conflict','unavailable'].includes(this.state.phase))return;
  if(!explicit&&this.state.graph?.nodes.some(node=>node.dragging||node.resizing))return;
  this.reading=true;const generation=++this.generation;
  try{const saved=parseWorkflowDraft(await this.request('read',undefined,this.abort.signal),this.id,this.user);
   if(generation!==this.generation||this.abort.signal.aborted)return;
   let graph=structuredClone(saved.graph);
   if(!explicit&&this.state.graph){
    if(this.state.saved?.revision===saved.revision&&this.state.saved.graph_sha256===saved.graph_sha256)graph=this.state.graph;
    else {const selected=new Set(this.state.graph.nodes.filter(n=>n.selected).map(n=>n.id));graph.nodes=graph.nodes.map(n=>selected.has(n.id)?{...n,selected:true}:n);}
   }
   this.publish({phase:'ready',saved,graph});
  }catch(problem){if(generation===this.generation)this.failed(problem);}finally{this.reading=false;}
 }
 change(input:FlowGraph):void{
  if(!['ready','saving'].includes(this.state.phase))return;
  let graph:FlowGraph;try{graph=editorGraph(input);}catch(problem){this.failed(problem);return;}
  const display=structuredClone(input);
  if(this.state.graph&&JSON.stringify(graph)===JSON.stringify(editorGraph(this.state.graph))){this.publish({...this.state,graph:display});return;}
  this.generation++;this.pending=graph;this.publish({...this.state,phase:'saving',graph:display});
  if(!this.writing)this.writing=this.write().finally(()=>{this.writing=null;});
 }
 private async write(){
  while(!this.abort.signal.aborted&&this.pending&&this.state.saved&&this.state.phase==='saving'){
   const previous=this.state.saved,graph=this.pending;this.pending=null;
   try{const saved=parseWorkflowDraft(await this.request('write',{expected_revision:previous.revision,graph},this.abort.signal),this.id,this.user);
    if(this.abort.signal.aborted)return;
    if(saved.revision<previous.revision||JSON.stringify(saved.graph)!==JSON.stringify(graph))return fail();
    this.publish({phase:this.pending?'saving':'ready',saved,graph:this.state.graph??structuredClone(saved.graph)});
   }catch(problem){this.pending=null;this.failed(problem);return;}
  }
 }
 async flush():Promise<boolean>{if(this.writing)await this.writing;return this.state.phase==='ready'&&!!this.state.saved;}
 async publishVersion():Promise<void>{
  if(!await this.flush()||!this.state.saved)return;
  const saved=this.state.saved;this.generation++;this.publish({...this.state,phase:'publishing'});
  try{
   const publication=parsePublication(await this.request('publish',{expected_revision:saved.revision},this.abort.signal),this.id);
   if(this.abort.signal.aborted)return;
   if(publication.draft_revision!==saved.revision)return fail();
   this.publish({...this.state,phase:'ready',saved:{...saved,publication}});
  }catch(problem){this.failed(problem);}
 }
 dispose(){if(this.state.phase==='closed')return;this.generation++;this.abort.abort();this.pending=null;this.publish({phase:'closed',saved:null,graph:null});}
}
