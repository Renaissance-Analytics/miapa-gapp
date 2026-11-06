import type {ChatState,ProviderChoice} from './agent-chat.js';
export interface AgentSettingsSnapshot {user_id:string;providers:ProviderChoice[];accounts:{id:string;provider:string;label:string;profile_assigned:boolean}[]}
export type SettingsKind='model'|'provider'|'connect'|'disconnect';
export interface SettingsControls {state:ChatState;busy:boolean;error:string;act:(captured:ChatState,kind:SettingsKind,value?:string)=>Promise<boolean>}
const object=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v);
const exact=(v:Record<string,unknown>,keys:string[])=>Object.keys(v).length===keys.length&&keys.every(k=>Object.hasOwn(v,k));
const provider=(v:unknown):v is string=>typeof v==='string'&&/^[a-z][a-z0-9-]{0,63}$/.test(v);
const label=(v:unknown):v is string=>typeof v==='string'&&!!v.trim()&&v.length<=80&&!/[\u0000-\u001f\u007f]/.test(v);
const uuid=(v:unknown):v is string=>typeof v==='string'&&/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(v);
const fail=():never=>{throw Error('Agent settings unavailable');};
export function parseSettings(v:unknown,user:string):AgentSettingsSnapshot{
 if(!object(v)||!exact(v,['user_id','providers','accounts'])||v.user_id!==user||!Array.isArray(v.providers)||v.providers.length>32||!Array.isArray(v.accounts)||v.accounts.length>64)return fail();
 const ids=new Set<string>(),accounts=new Set<string>();
 const providers=v.providers.map(p=>{
  if(!object(p)||!exact(p,['id','label','available'])||!provider(p.id)||!label(p.label)||typeof p.available!=='boolean'||ids.has(p.id))return fail();
  ids.add(p.id);return {id:p.id,label:p.label,available:p.available};
 });
 const rows=v.accounts.map(a=>{
  if(!object(a)||!exact(a,['id','provider','label','profile_assigned'])||!uuid(a.id)||!provider(a.provider)||!label(a.label)||typeof a.profile_assigned!=='boolean'||accounts.has(a.id))return fail();
  accounts.add(a.id);return {id:a.id,provider:a.provider,label:a.label,profile_assigned:a.profile_assigned};
 });
 return {user_id:user,providers,accounts:rows};
}
export async function readSettings(user:string,signal:AbortSignal,request:typeof fetch=fetch):Promise<AgentSettingsSnapshot>{
 if(!/^[0-9]+$/.test(user))return fail();
 const r=await request('/api/agent/settings/',{credentials:'same-origin',cache:'no-store',redirect:'error',headers:{'X-Maipa-Agent-Actor':user},signal:AbortSignal.any([signal,AbortSignal.timeout(10000)])});
 if(r.status!==200)return fail();return parseSettings(await r.json(),user);
}
export function settingsCommand(s:ChatState,kind:SettingsKind,value=''):Record<string,unknown>{
 const selection_revision=s.connection.revision;
 if(kind==='provider'){
  if(!['offline','unavailable'].includes(s.phase)||!s.providers.some(p=>p.id===value&&p.available))return fail();
  return {action:'select_provider',selection_revision,provider:value};
 }
 if(!s.connection.account_id||!s.connection.available)return fail();
 if(kind==='model'){
  if(s.phase!=='ready'||!s.models?.availableModels.some(m=>m.modelId===value))return fail();
  return {action:'set_model',revision:s.revision,selection_revision,model_id:value};
 }
 if(kind==='connect'&&['offline','unavailable'].includes(s.phase))return {action:'connect',revision:s.revision,selection_revision};
 if(kind==='disconnect'&&s.phase==='ready')return {action:'disconnect',selection_revision};
 return fail();
}
/** A controlled component cannot retarget a delayed click to a newer connection. */
export async function applySettingsCommand(captured:ChatState,current:ChatState,kind:SettingsKind,value:string,invoke:(command:Record<string,unknown>)=>Promise<boolean>):Promise<boolean>{
 if(captured.connection.account_id!==current.connection.account_id||captured.connection.revision!==current.connection.revision||captured.revision!==current.revision)return false;
 try{return await invoke(settingsCommand(captured,kind,value));}catch{return false;}
}
