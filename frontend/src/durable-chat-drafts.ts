import type {PromptDraft} from '@particle-academy/react-fancy';
import {csrfToken} from './api.js';
import {emptyDraft} from './chat-drafts.js';

type Phase='loading'|'ready'|'unsaved'|'saving'|'conflict'|'closed';
interface Pending {id:string;text:string;draft:PromptDraft}
interface Saved {account_id:string|null;revision:number;draft:PromptDraft;pending:Pending|null;accepted:boolean}
interface Entry {draft:PromptDraft;pending:Pending|null;revision:number;generation:number;dirty:boolean;loaded:boolean;phase:Phase;job:Promise<void>|null;submittedGeneration:number|null;confirmedPendingId:string|null;timer?:ReturnType<typeof setTimeout>}
type Request=(account:string,command?:{expected_revision:number;draft:PromptDraft;pending:Pending|null})=>Promise<unknown>;
const object=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v);
const uuid=(v:unknown):v is string=>typeof v==='string'&&/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(v);
const exact=(v:Record<string,unknown>,keys:string[])=>Object.keys(v).length===keys.length&&keys.every(k=>Object.hasOwn(v,k));
const same=(a:unknown,b:unknown)=>JSON.stringify(a)===JSON.stringify(b);
function draft(v:unknown):PromptDraft{
 if(!object(v)||!exact(v,['v','text','pills','attachments'])||v.v!==1||typeof v.text!=='string'||v.text.length>32768
  ||!Array.isArray(v.pills)||v.pills.length>24||!Array.isArray(v.attachments)||v.attachments.length>8||new TextEncoder().encode(JSON.stringify(v)).length>65536)throw Error('Invalid draft');
 return structuredClone(v) as unknown as PromptDraft;
}
function saved(v:unknown,account:string):Saved{
 if(!object(v)||!exact(v,['account_id','revision','draft','pending','accepted'])||v.account_id!==(account==='unselected'?null:account)
  ||!Number.isSafeInteger(v.revision)||Number(v.revision)<0||Number(v.revision)>=2e9||typeof v.accepted!=='boolean')throw Error('Invalid draft receipt');
 const d=draft(v.draft);let pending:Pending|null=null;
 if(v.pending!==null){
  const p=v.pending;if(!object(p)||!exact(p,['id','text','draft'])||!uuid(p.id)||typeof p.text!=='string'||!p.text.trim()||p.text.length>8000)throw Error('Invalid draft retry');
  pending={id:p.id,text:p.text,draft:draft(p.draft)};
 }else if(v.accepted)throw Error('Invalid accepted receipt');
 return {account_id:v.account_id as string|null,revision:Number(v.revision),draft:d,pending,accepted:v.accepted};
}
class Closed extends Error{}

/** Saves are scoped to their captured account; restoration never sends a turn. */
export class DurableChatDrafts{
 private entries=new Map<string,Entry>();private readonly abort=new AbortController();
 private readonly request:Request;
 constructor(readonly user:string,private changed:()=>void,requestOverride?:Request){
  this.request=requestOverride??(async(account,command)=>{
   if(!/^[0-9]+$/.test(user)||account!=='unselected'&&!uuid(account))throw new Closed();
   const headers:Record<string,string>={'X-Maipa-Agent-Actor':user};
   if(command){headers['Content-Type']='application/json';headers['X-CSRFToken']=await csrfToken(fetch);}
   if(this.abort.signal.aborted)throw new Closed();
   const r=await fetch('/api/agent/drafts/'+account+'/',{method:command?'POST':'GET',headers,credentials:'same-origin',cache:'no-store',redirect:'error',
    signal:AbortSignal.any([this.abort.signal,AbortSignal.timeout(10000)]),...(command?{body:JSON.stringify(command)}:{})});
   if([401,403,404].includes(r.status))throw new Closed();
   if(r.status===409){const v:unknown=await r.json();if(object(v)&&object(v.error)&&v.error.code==='actor_changed')throw new Closed();}
   if(r.status!==200)throw Error('Draft could not be confirmed');return r.json();
  });
 }
 private entry(account:string):Entry{
  let e=this.entries.get(account);if(!e){e={draft:emptyDraft(),pending:null,revision:0,generation:0,dirty:false,loaded:false,phase:'loading',job:null,submittedGeneration:null,confirmedPendingId:null};this.entries.set(account,e);}return e;
 }
 private publish(){if(!this.abort.signal.aborted)this.changed();}
 private schedule(account:string,e:Entry){
  clearTimeout(e.timer);if(this.abort.signal.aborted||e.phase==='conflict')return;
  e.timer=setTimeout(()=>{void this.save(account);},400);
 }
 read(account:string):PromptDraft{return this.abort.signal.aborted?emptyDraft():structuredClone(this.entry(account).draft);}
 status(account:string):Phase{return this.abort.signal.aborted?'closed':this.entry(account).phase;}
 write(account:string,value:PromptDraft){
  if(this.abort.signal.aborted)return;const e=this.entry(account);e.draft=structuredClone(value);e.generation++;e.dirty=true;
  if(e.phase!=='conflict'){e.phase=e.loaded?'unsaved':'loading';this.schedule(account,e);}this.publish();
 }
 async load(account:string){
  if(this.abort.signal.aborted)return;const e=this.entry(account);if(e.job)return e.job;if(e.loaded)return;
  const generation=e.generation;
  e.job=(async()=>{
   try{
    const value=saved(await this.request(account),account);if(this.abort.signal.aborted)return;
    e.revision=value.revision;e.loaded=true;e.pending=value.pending;e.confirmedPendingId=value.pending?.id??null;
    if(e.generation===generation&&!e.dirty)e.draft=value.draft;
    else if(!same(value.draft,emptyDraft())){e.phase='conflict';this.publish();return;}
    if(value.accepted&&value.pending){if(same(e.draft,value.pending.draft)){e.draft=emptyDraft();e.generation++;}e.pending=null;e.dirty=true;}
    e.phase=e.dirty?'unsaved':'ready';this.publish();if(e.dirty)this.schedule(account,e);
   }catch(error){if(error instanceof Closed){this.dispose();return;}if(!this.abort.signal.aborted){e.phase='conflict';this.publish();}}
  })();try{await e.job;}finally{e.job=null;}
 }
 async save(account:string){
  const e=this.entry(account);clearTimeout(e.timer);while(e.job)await e.job;
  if(this.abort.signal.aborted||e.phase==='conflict')return;
  if(!e.loaded)await this.load(account);
  if(this.abort.signal.aborted||this.status(account)==='conflict'||!e.loaded||!e.dirty)return;
  const generation=e.generation,command={expected_revision:e.revision,draft:structuredClone(e.draft),pending:structuredClone(e.pending)};
  e.phase='saving';this.publish();
  e.job=(async()=>{
   try{
    const value=saved(await this.request(account,command),account);
    if(this.abort.signal.aborted)return;
    if(value.revision!==command.expected_revision+1||!same(value.draft,command.draft)||!same(value.pending,command.pending))throw Error('Draft receipt mismatch');
    e.revision=value.revision;e.confirmedPendingId=value.pending?.id??null;e.dirty=e.generation!==generation;e.phase=e.dirty?'unsaved':'ready';this.publish();
   }catch(error){if(error instanceof Closed){this.dispose();return;}if(!this.abort.signal.aborted){e.phase='conflict';this.publish();}}
  })();try{await e.job;}finally{e.job=null;}
 }
 async prepare(account:string,text:string):Promise<string>{
  const e=this.entry(account),submitted=structuredClone(e.draft),generation=e.generation;
  while(e.job)await e.job;if(!e.loaded)await this.load(account);
  if(this.abort.signal.aborted||!e.loaded||e.phase==='conflict')throw Error('Draft needs readback');
  if(!e.pending||e.pending.text!==text){e.pending={id:crypto.randomUUID(),text,draft:submitted};e.dirty=true;}
  const id=e.pending.id;e.submittedGeneration=generation;
  await this.save(account);
  if(this.abort.signal.aborted||this.status(account)==='conflict'||e.confirmedPendingId!==id)throw Error('Draft could not be saved');
  return id;
 }
 async acknowledge(account:string,id:string){
  const e=this.entry(account);if(this.abort.signal.aborted||e.pending?.id!==id)return;
  if(e.generation===e.submittedGeneration&&same(e.draft,e.pending.draft))e.draft=emptyDraft();e.pending=null;e.submittedGeneration=null;e.dirty=true;e.generation++;
  if(e.phase!=='conflict')await this.save(account);this.publish();
 }
 async resolve(account:string,keepLocal:boolean){
  const e=this.entry(account);while(e.job)await e.job;if(this.abort.signal.aborted)return;
  clearTimeout(e.timer);const generation=e.generation;e.phase='loading';this.publish();
  e.job=(async()=>{
   try{
    const value=saved(await this.request(account),account);if(this.abort.signal.aborted)return;
    e.revision=value.revision;e.loaded=true;e.confirmedPendingId=value.pending?.id??null;
    if(!keepLocal&&e.generation===generation){e.draft=value.draft;e.pending=value.pending;e.dirty=false;}
    else e.dirty=true;
    if(value.accepted&&value.pending&&e.pending?.id===value.pending.id){if(same(e.draft,value.pending.draft))e.draft=emptyDraft();e.pending=null;e.dirty=true;}
    e.phase=e.dirty?'unsaved':'ready';this.publish();
   }catch(error){if(error instanceof Closed)this.dispose();else if(!this.abort.signal.aborted){e.phase='conflict';this.publish();}}
  })();try{await e.job;}finally{e.job=null;}
  if(e.dirty&&this.status(account)!=='conflict'&&!this.abort.signal.aborted)await this.save(account);
 }
 dispose(){if(this.abort.signal.aborted)return;this.abort.abort();for(const e of this.entries.values())clearTimeout(e.timer);this.entries.clear();this.changed();}
}
