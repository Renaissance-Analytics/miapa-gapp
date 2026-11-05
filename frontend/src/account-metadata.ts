import {csrfToken} from './api.js';

export interface AccountMetadata {user_id:string;account_id:string;provider:string;label:string;profile_assigned:boolean;revision:number}
export interface MetadataState {phase:'loading'|'ready'|'saving'|'conflict'|'unavailable'|'closed';value:AccountMetadata|null;latest:AccountMetadata|null;draft:string}
const object=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v);
const uuid=(v:unknown):v is string=>typeof v==='string'&&/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(v);
const validLabel=(v:unknown):v is string=>typeof v==='string'&&!!v&&v===v.trim()&&v.length<=80&&!/[\u0000-\u001f\u007f-\u009f]/.test(v);
const unavailable=()=>Error('Account name could not be confirmed');
class MetadataConflict extends Error {}

export function parseAccountMetadata(v:unknown,user:string,account:string):AccountMetadata {
 if(!object(v)||Object.keys(v).length!==6||!['user_id','account_id','provider','label','profile_assigned','revision'].every(k=>Object.hasOwn(v,k))
  ||!/^\d+$/.test(user)||!uuid(account)||v.user_id!==user||v.account_id!==account
  ||typeof v.provider!=='string'||!/^[a-z][a-z0-9-]{0,63}$/.test(v.provider)||!validLabel(v.label)
  ||typeof v.profile_assigned!=='boolean'||!Number.isInteger(v.revision)||(v.revision as number)<0||(v.revision as number)>=2_000_000_000)throw unavailable();
 return {user_id:user,account_id:account,provider:v.provider,label:v.label,profile_assigned:v.profile_assigned,revision:v.revision as number};
}

function scope(user:string,account:string){if(!/^\d+$/.test(user)||!uuid(account))throw unavailable();return `/api/agent/accounts/${account}/metadata/`;}
function options(user:string,signal:AbortSignal):RequestInit{return {credentials:'same-origin',cache:'no-store',redirect:'error',headers:{'X-Maipa-Agent-Actor':user},signal:AbortSignal.any([signal,AbortSignal.timeout(10000)])};}

export async function readAccountMetadata(user:string,account:string,signal:AbortSignal,request:typeof fetch=fetch):Promise<AccountMetadata> {
 const response=await request(scope(user,account),options(user,signal));
 if(response.status!==200)throw unavailable();return parseAccountMetadata(await response.json(),user,account);
}

export async function saveAccountMetadata(user:string,base:AccountMetadata,label:string,signal:AbortSignal,request:typeof fetch=fetch):Promise<AccountMetadata> {
 const path=scope(user,base.account_id);parseAccountMetadata(base,user,base.account_id);if(!validLabel(label)||signal.aborted)throw unavailable();
 const token=await csrfToken(request);if(signal.aborted)throw unavailable();
 const response=await request(path,{...options(user,signal),method:'POST',headers:{'Content-Type':'application/json','X-Maipa-Agent-Actor':user,'X-CSRFToken':token},body:JSON.stringify({expected_revision:base.revision,label})});
 if(response.status===409)throw new MetadataConflict();if(response.status!==200)throw unavailable();
 const value=parseAccountMetadata(await response.json(),user,base.account_id);
 if(value.provider!==base.provider||value.label!==label||value.revision!==base.revision+(label===base.label?0:1))throw unavailable();
 return value;
}

/** One mounted principal/account; acknowledged metadata and unsent text stay separate. */
export class AccountMetadataController {
 state:MetadataState={phase:'loading',value:null,latest:null,draft:''};
 private lifetime=new AbortController();
 private pending=false;
 private sequence=0;
 constructor(private user:string,private account:string,private changed:(value:MetadataState)=>void,private request:typeof fetch=fetch){scope(user,account);}
 private publish(value:MetadataState){if(!this.lifetime.signal.aborted){this.state=value;this.changed(value);}}
 async load(){
  if(this.lifetime.signal.aborted||this.pending)return;
  this.pending=true;const sequence=++this.sequence;const before=this.state;this.publish({...before,phase:'loading',latest:null});
  try{
   const value=await readAccountMetadata(this.user,this.account,this.lifetime.signal,this.request);
   if(this.lifetime.signal.aborted||sequence!==this.sequence)return;
   if(before.value&&(value.provider!==before.value.provider||value.revision<before.value.revision))throw unavailable();
   const edited=!!before.value&&(before.draft!==before.value.label||this.state.draft!==before.draft);
   if(before.value&&edited&&(value.revision!==before.value.revision||value.label!==before.value.label)){
    this.publish({...this.state,phase:'conflict',value:before.value,latest:value});
   }else this.publish({...this.state,phase:'ready',value,latest:null,draft:edited?this.state.draft:value.label});
  }catch{this.publish({...this.state,phase:'unavailable'});}
  finally{this.pending=false;}
 }
 edit(draft:string){if(!this.lifetime.signal.aborted&&this.state.value)this.publish({...this.state,draft});}
 async save(){
  if(this.lifetime.signal.aborted||this.pending||this.state.phase!=='ready'||!this.state.value)return false;
  const base=this.state.value,draft=this.state.draft,label=draft.trim();if(!validLabel(label)||label===base.label)return false;
  this.pending=true;this.publish({...this.state,phase:'saving',latest:null});
  try{
   const value=await saveAccountMetadata(this.user,base,label,this.lifetime.signal,this.request);
   if(this.lifetime.signal.aborted)return false;
   this.publish({...this.state,phase:'ready',value,latest:null,draft:this.state.draft===draft?value.label:this.state.draft});return true;
  }catch(error){this.publish({...this.state,phase:error instanceof MetadataConflict?'conflict':'unavailable'});return false;}
  finally{this.pending=false;}
 }
 keepDraft(){if(!this.pending&&this.state.latest)this.publish({...this.state,phase:'ready',value:this.state.latest,latest:null});}
 useSaved(){if(!this.pending&&this.state.latest)this.publish({...this.state,phase:'ready',value:this.state.latest,draft:this.state.latest.label,latest:null});}
 close(){if(!this.lifetime.signal.aborted){this.lifetime.abort();this.sequence++;this.state={...this.state,phase:'closed'};}}
}
