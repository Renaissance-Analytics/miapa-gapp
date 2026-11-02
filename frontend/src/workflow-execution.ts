import {csrfToken} from './api.js';
import {parsePublication,WorkflowError,type Publication} from './shared-workflow.js';

export interface Worker {available:boolean;state:'running'|'starting'|'stopping'|'not_running'|'failed'}
export interface WorkflowRun {id:string;definition_id:string;definition_revision:number;schema_sha256:string;runtime:string;step:number;status:'queued'|'running'|'waiting'|'blocked'|'completed'|'failed'|'cancelled';error:string|null;outputs:Record<string,unknown>;nodes:Record<string,{status:string;attempts:number;error:string|null;ports:string[]}>}
export interface HumanField {key:string;label:string;type:'text'|'textarea'|'checkbox';required?:boolean}
export interface HumanGate {id:string;run_id:string;node_id:string;kind:'human_approval'|'user_input';step:number;definition_revision:number;schema_sha256:string;request_sha256:string;request:{title:string;description?:string|null;fields?:HumanField[]};input:unknown}
export interface RunValue {run:WorkflowRun;worker:Worker;gate:HumanGate|null}
export interface ExecutionSnapshot {draft_id:string;draft_revision:number;publication:Publication|null;worker:Worker;runs:WorkflowRun[]}
export interface ExecutionState {phase:'loading'|'ready'|'pending'|'unavailable'|'closed';snapshot:ExecutionSnapshot|null;value:RunValue|null}
type Action='snapshot'|'inspect'|'run'|'cancel'|'resume'|'answer';
type Request=(action:Action,command?:Record<string,unknown>,signal?:AbortSignal)=>Promise<unknown>;
const object=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v);
const uuid=(v:unknown):v is string=>typeof v==='string'&&/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(v);
const count=(v:unknown):v is number=>Number.isSafeInteger(v)&&Number(v)>=0&&Number(v)<2e9;
const hash=(v:unknown)=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const exact=(v:Record<string,unknown>,keys:string[])=>Object.keys(v).length===keys.length&&keys.every(k=>Object.hasOwn(v,k));
const fail=():never=>{throw new WorkflowError('unavailable');};
function worker(v:unknown):Worker{
 if(!object(v)||!exact(v,['available','state'])||typeof v.available!=='boolean'||!['running','starting','stopping','not_running','failed'].includes(String(v.state))||v.available!==(v.state==='running'))return fail();
 return v as unknown as Worker;
}
function run(v:unknown,definition?:string):WorkflowRun{
 if(!object(v)||!exact(v,['id','definition_id','definition_revision','schema_sha256','runtime','step','status','error','outputs','nodes'])
  ||!uuid(v.id)||!uuid(v.definition_id)||definition!==undefined&&v.definition_id!==definition||!count(v.definition_revision)||v.definition_revision<1||!hash(v.schema_sha256)
  ||typeof v.runtime!=='string'||v.runtime.length>32||!count(v.step)||!['queued','running','waiting','blocked','completed','failed','cancelled'].includes(String(v.status))
  ||v.error!==null&&(typeof v.error!=='string'||v.error.length>128)||!object(v.outputs)||!object(v.nodes)||Object.keys(v.nodes).length>30)return fail();
 for(const [key,n] of Object.entries(v.nodes))if(!/^[A-Za-z0-9_-]{1,80}$/.test(key)||!object(n)||!exact(n,['status','attempts','error','ports'])
  ||!['claimed','completed','skipped','failed','paused','released'].includes(String(n.status))||!count(n.attempts)||n.error!==null&&(typeof n.error!=='string'||n.error.length>128)
  ||!Array.isArray(n.ports)||n.ports.length>16||n.ports.some(p=>typeof p!=='string'||p.length>80))return fail();
 return structuredClone(v) as unknown as WorkflowRun;
}
function gate(v:unknown,r:WorkflowRun):HumanGate|null{
 if(v===null)return null;
 if(!object(v)||!exact(v,['id','run_id','node_id','kind','step','definition_revision','schema_sha256','request_sha256','request','input'])
  ||!uuid(v.id)||v.run_id!==r.id||typeof v.node_id!=='string'||!/^[A-Za-z0-9_-]{1,80}$/.test(v.node_id)||r.status!=='waiting'||v.step!==r.step
  ||v.definition_revision!==r.definition_revision||v.schema_sha256!==r.schema_sha256||!hash(v.request_sha256)||!object(v.request)||typeof v.request.title!=='string'||v.request.title.length>120)return fail();
 if(v.kind==='human_approval'){
  if(!exact(v.request,['title','description'])||v.request.description!==null&&(typeof v.request.description!=='string'||v.request.description.length>2000))return fail();
 }else if(v.kind==='user_input'){
  if(!exact(v.request,['title','fields'])||!Array.isArray(v.request.fields)||!v.request.fields.length||v.request.fields.length>12)return fail();
  const keys=new Set<string>();
  for(const f of v.request.fields){if(!object(f)||typeof f.key!=='string'||!/^[A-Za-z0-9_-]{1,80}$/.test(f.key)||keys.has(f.key)||typeof f.label!=='string'||!f.label.trim()||f.label.length>120
   ||!['text','textarea','checkbox'].includes(String(f.type))||Object.keys(f).some(k=>!['key','label','type','required'].includes(k))||f.required!==undefined&&typeof f.required!=='boolean')return fail();keys.add(f.key);}
 }else return fail();
 return structuredClone(v) as unknown as HumanGate;
}
export function parseExecution(v:unknown,draft:string,definition?:string):ExecutionSnapshot|RunValue{
 if(!uuid(draft)||!object(v)||new TextEncoder().encode(JSON.stringify(v)).length>2_000_000)return fail();
 if(Object.hasOwn(v,'runs')){
  if(!exact(v,['draft_id','draft_revision','publication','worker','runs'])||v.draft_id!==draft||!count(v.draft_revision)||!Array.isArray(v.runs)||v.runs.length>10)return fail();
  const publication=v.publication===null?null:parsePublication(v.publication,draft);
  if(publication&&publication.draft_revision>v.draft_revision||!publication&&v.runs.length)return fail();
  const runs=v.runs.map(r=>run(r,publication?.definition_id));
  if(new Set(runs.map(r=>r.id)).size!==runs.length||publication&&runs.some(r=>r.definition_revision>publication.definition_revision))return fail();
  return {draft_id:draft,draft_revision:v.draft_revision,publication,worker:worker(v.worker),runs};
 }
 if(!exact(v,['run','worker','gate']))return fail();const r=run(v.run,definition);
 return {run:r,worker:worker(v.worker),gate:gate(v.gate,r)};
}

function request(user:string,draft:string):Request{
 return async(action,command={},signal)=>{
  if(!/^[0-9]+$/.test(user)||!uuid(draft))return fail();
  const {run_id,gate_id,...body}=command;
  if(!['snapshot','run'].includes(action)&&!uuid(run_id)||action==='answer'&&!uuid(gate_id))return fail();
  const path=action==='snapshot'||action==='run'?`drafts/${draft}/execution/`:action==='answer'?`runs/${run_id}/gates/${gate_id}/response/`:`runs/${run_id}/${action==='inspect'?'':action+'/'}`;
  const read=['snapshot','inspect'].includes(action),headers:Record<string,string>={'X-Maipa-Workflow-Actor':user};
  if(!read){headers['Content-Type']='application/json';headers['X-CSRFToken']=await csrfToken(fetch);}
  if(signal?.aborted)throw new WorkflowError('closed');
  const result=await fetch('/api/workflows/'+path,{method:read?'GET':'POST',headers,credentials:'same-origin',cache:'no-store',redirect:'error',
   signal:signal?AbortSignal.any([signal,AbortSignal.timeout(10000)]):AbortSignal.timeout(10000),...(!read?{body:JSON.stringify(body)}:{})});
  if([401,403,404].includes(result.status))throw new WorkflowError('closed');
  if(result.status===409){const problem:unknown=await result.json();if(object(problem)&&object(problem.error)&&problem.error.code==='actor_changed')throw new WorkflowError('closed');return fail();}
  if(result.status!==200)return fail();return result.json();
 };
}

/** Keeps acknowledged execution separate from mutable editor state and model prose. */
export class WorkflowExecutionController{
 state:ExecutionState={phase:'loading',snapshot:null,value:null};
 private readonly abort=new AbortController();private readonly request:Request;private busy=false;
 private reading:Promise<void>|null=null;
 private selected:string|null=null;private pendingRun:{expected_revision:number;definition_revision:number;request_key:string;seed:Record<string,unknown>}|null=null;
 private pendingAnswer:{gate_id:string;request_key:string;answer:unknown}|null=null;
 constructor(readonly user:string,readonly draft:string,readonly changed:(state:ExecutionState)=>void,requestOverride?:Request){this.request=requestOverride??request(user,draft);}
 private publish(state:ExecutionState){if(!this.abort.signal.aborted){this.state=state;this.changed(state);}}
 private failed(error:unknown){if(error instanceof WorkflowError&&error.kind==='closed')this.dispose();else this.publish({...this.state,phase:'unavailable'});}
 async load(selected?:string){
  if(this.busy||this.abort.signal.aborted)return;this.busy=true;
  let finished!:()=>void;this.reading=new Promise<void>(resolve=>{finished=resolve;});
  try{
   const s=parseExecution(await this.request('snapshot',undefined,this.abort.signal),this.draft) as ExecutionSnapshot;
   if(selected!==undefined){if(!s.runs.some(r=>r.id===selected))return fail();this.selected=selected;}
   this.selected??=s.runs[0]?.id??null;
   const value=this.selected?parseExecution(await this.request('inspect',{run_id:this.selected},this.abort.signal),this.draft,s.publication?.definition_id) as RunValue:null;
   if(value&&value.run.id!==this.selected)return fail();
   this.publish({phase:'ready',snapshot:s,value});
  }catch(e){this.failed(e);}finally{this.busy=false;this.reading=null;finished();}
 }
 async run(revision:number,definitionRevision:number,seed:Record<string,unknown>){
  if(this.reading)await this.reading;
  if(this.busy||this.abort.signal.aborted||this.state.phase!=='ready')return;
  const s=this.state.snapshot,p=s?.publication;
  if(!s?.worker.available||!p||s.draft_revision!==revision||p.draft_revision!==revision||p.definition_revision!==definitionRevision)return;
  if(this.pendingRun&&(this.pendingRun.expected_revision!==revision||this.pendingRun.definition_revision!==definitionRevision||JSON.stringify(this.pendingRun.seed)!==JSON.stringify(seed))){this.publish({...this.state,phase:'unavailable'});return;}
  this.pendingRun??={expected_revision:revision,definition_revision:definitionRevision,request_key:crypto.randomUUID(),seed:structuredClone(seed)};
  await this.mutate('run',this.pendingRun);if(this.state.phase==='ready')this.pendingRun=null;
 }
 async control(action:'cancel'|'resume'){
  const v=this.state.value;if(!v||this.state.phase!=='ready')return;
  if(this.reading)await this.reading;
  if(this.abort.signal.aborted||this.state.phase!=='ready')return;
  await this.mutate(action,{run_id:v.run.id,expected_step:v.run.step});
 }
 async answer(answer:unknown){
  const gate=this.state.value?.gate;if(!gate||this.state.phase!=='ready')return;
  if(this.reading)await this.reading;
  if(this.abort.signal.aborted||this.state.phase!=='ready'||this.state.value?.gate?.id!==gate.id)return;
  if(this.pendingAnswer?.gate_id!==gate.id)this.pendingAnswer={gate_id:gate.id,request_key:crypto.randomUUID(),answer:structuredClone(answer)};
  if(JSON.stringify(this.pendingAnswer.answer)!==JSON.stringify(answer)){this.publish({...this.state,phase:'unavailable'});return;}
  if(this.busy||this.abort.signal.aborted)return;this.busy=true;this.publish({...this.state,phase:'pending'});
  try{
   const receipt=await this.request('answer',{run_id:gate.run_id,gate_id:gate.id,expected_step:gate.step,definition_revision:gate.definition_revision,
    request_sha256:gate.request_sha256,request_key:this.pendingAnswer.request_key,answer},this.abort.signal);
   if(!object(receipt)||!exact(receipt,['status','id','run_id','gate_id','accepted_step','next_step'])||receipt.status!=='recorded'||!uuid(receipt.id)||receipt.run_id!==gate.run_id||receipt.gate_id!==gate.id||receipt.accepted_step!==gate.step||receipt.next_step!==gate.step+1)return fail();
   this.pendingAnswer=null;this.publish({...this.state,phase:'ready'});
  }catch(e){this.failed(e);}finally{this.busy=false;}
  if(this.state.phase==='ready')await this.load();
 }
 private async mutate(action:'run'|'cancel'|'resume',command:Record<string,unknown>){
  if(this.busy||this.abort.signal.aborted)return;this.busy=true;this.publish({...this.state,phase:'pending'});
  try{
   const value=parseExecution(await this.request(action,command,this.abort.signal),this.draft,this.state.snapshot?.publication?.definition_id) as RunValue;
   if(action!=='run'&&value.run.id!==command.run_id)return fail();
   this.selected=value.run.id;this.publish({...this.state,phase:'ready',value});
  }catch(e){this.failed(e);}finally{this.busy=false;}
 }
 dispose(){if(this.abort.signal.aborted)return;this.abort.abort();this.state={phase:'closed',snapshot:null,value:null};this.changed(this.state);}
}
