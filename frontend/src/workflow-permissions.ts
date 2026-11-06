import {csrfToken} from './api.js';
import {WorkflowError} from './shared-workflow.js';

export interface WorkflowConnection {accountId:string;leaseId:string}
export interface WorkflowPermission {draft_id:string;draft_revision:number;account_id:string;ui_id:string;entity_id:string;
 entity_name:string;policy_version:number;can_manage:boolean;enabled:boolean;expires_at:string|null}
interface PermissionState {phase:'loading'|'ready'|'pending'|'unavailable'|'closed';value:WorkflowPermission|null}
type Command={expected_revision:number;expected_policy_version:number;enabled:boolean};
type Request=(command?:Command,signal?:AbortSignal)=>Promise<unknown>;
const uuid=(v:unknown):v is string=>typeof v==='string'&&/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(v);
const count=(v:unknown):v is number=>Number.isSafeInteger(v)&&Number(v)>=0&&Number(v)<2e9;
const fail=():never=>{throw new WorkflowError('unavailable');};
export function parseWorkflowPermission(v:unknown,draft:string,binding:WorkflowConnection):WorkflowPermission{
 if(!v||typeof v!=='object'||Array.isArray(v))return fail();const row=v as Record<string,unknown>;
 const keys=['draft_id','draft_revision','account_id','ui_id','entity_id','entity_name','policy_version','can_manage','enabled','expires_at'];
 if(Object.keys(row).length!==keys.length||keys.some(k=>!Object.hasOwn(row,k))||!uuid(draft)||row.draft_id!==draft
  ||!uuid(binding.accountId)||row.account_id!==binding.accountId||!uuid(binding.leaseId)||row.ui_id!==binding.leaseId||!uuid(row.entity_id)
  ||!count(row.draft_revision)||!count(row.policy_version)||typeof row.can_manage!=='boolean'||typeof row.enabled!=='boolean'
  ||typeof row.entity_name!=='string'||!row.entity_name.trim()||row.entity_name.length>256
  ||row.expires_at!==null&&(typeof row.expires_at!=='string'||row.expires_at.length>40||!/^\d{4}-\d\d-\d\dT/.test(row.expires_at)||!Number.isFinite(Date.parse(row.expires_at)))
  ||row.enabled&&row.expires_at===null)return fail();
 return structuredClone(row) as unknown as WorkflowPermission;
}
function request(user:string,draft:string,binding:WorkflowConnection):Request{
 return async(command,signal)=>{
  if(!/^[0-9]+$/.test(user)||!uuid(draft)||!uuid(binding.accountId)||!uuid(binding.leaseId))return fail();
  const headers:Record<string,string>={'X-Maipa-Workflow-Actor':user};
  if(command){headers['Content-Type']='application/json';headers['X-CSRFToken']=await csrfToken(fetch);}
  if(signal?.aborted)throw new WorkflowError('closed');
  const response=await fetch(`/api/workflows/drafts/${draft}/agent-authority/${binding.accountId}/${binding.leaseId}/`,{
   method:command?'POST':'GET',headers,credentials:'same-origin',cache:'no-store',redirect:'error',
   signal:signal?AbortSignal.any([signal,AbortSignal.timeout(10000)]):AbortSignal.timeout(10000),...(command?{body:JSON.stringify(command)}:{})});
  if([401,403,404].includes(response.status))throw new WorkflowError('closed');
  if(response.status===409){const v=await response.json();if(v?.error?.code==='actor_changed')throw new WorkflowError('closed');return fail();}
  if(response.status!==200)return fail();return response.json();
 };
}

/** Human desired-state command; an uncertain response never triggers a grant retry. */
export class WorkflowPermissionController{
 state:PermissionState={phase:'loading',value:null};
 private readonly abort=new AbortController();private busy=false;private readonly request:Request;
 private reading:Promise<void>|null=null;
 constructor(readonly user:string,readonly draft:string,readonly binding:WorkflowConnection,readonly changed:(state:PermissionState)=>void,override?:Request){
  this.request=override??request(user,draft,binding);
 }
 private publish(value:PermissionState){if(!this.abort.signal.aborted){this.state=value;this.changed(value);}}
 private failed(error:unknown){if(error instanceof WorkflowError&&error.kind==='closed')this.dispose();else this.publish({...this.state,phase:'unavailable'});}
 async load(){
  if(this.busy||this.abort.signal.aborted)return;this.busy=true;
  let finished!:()=>void;this.reading=new Promise<void>(resolve=>{finished=resolve;});
  try{const value=parseWorkflowPermission(await this.request(undefined,this.abort.signal),this.draft,this.binding);this.publish({phase:'ready',value});}
  catch(e){this.failed(e);}finally{this.busy=false;this.reading=null;finished();}
 }
 async setEnabled(enabled:boolean,revision:number){
  const seen=this.state.value;
  if(this.reading)await this.reading;
  if(this.busy||this.abort.signal.aborted||this.state.phase!=='ready'||!this.state.value?.can_manage||!seen?.can_manage||seen.draft_revision!==revision)return;
  this.busy=true;this.publish({...this.state,phase:'pending'});
  try{
   const value=parseWorkflowPermission(await this.request({expected_revision:revision,expected_policy_version:seen.policy_version,enabled},this.abort.signal),this.draft,this.binding);
   if(value.enabled!==enabled||value.draft_revision!==revision||value.policy_version<seen.policy_version)return fail();
   this.publish({phase:'ready',value});
  }catch(e){this.failed(e);}finally{this.busy=false;}
 }
 dispose(){this.abort.abort();this.state={phase:'closed',value:null};this.changed(this.state);}
}
