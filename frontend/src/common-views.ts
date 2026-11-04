import type {ChatState,Composition} from './agent-chat.js';
import {viewUiRequest} from './personal-views.js';

export type CommonViewKey='agent-settings'|'clients'|'users';
export interface CommonView {key:CommonViewKey;title:string;icon:string;composition:NonNullable<Composition['composition']>}
const definitions:CommonView[]=[
 {key:'agent-settings',title:'Agent settings',icon:'settings',composition:{title:'Agent settings',schema:{id:'common-agent-settings',type:'Maipa.AgentSettings'}}},
 {key:'clients',title:'Clients',icon:'contact',composition:{title:'Clients',schema:{id:'common-clients',type:'Maipa.ClientCatalog'}}},
 {key:'users',title:'User management',icon:'users',composition:{title:'User management',schema:{id:'common-users',type:'Maipa.AdminUsers'}}},
];
const unavailable=()=>Error('The view could not be opened. Try its header shortcut again.');
const object=(v:unknown):v is Record<string,unknown>=>typeof v==='object'&&v!==null&&!Array.isArray(v);
const exact=(v:Record<string,unknown>,keys:string[])=>Object.keys(v).sort().join(',')===keys.sort().join(',');
const same=(a:unknown,b:unknown)=>JSON.stringify(a)===JSON.stringify(b);

/** Visibility hints only; registered components and the shared UI service enforce authority. */
export async function readCommonViews(user:string,signal:AbortSignal,fetcher:typeof fetch=fetch):Promise<CommonView[]>{
 if(!/^[0-9]+$/.test(user)||signal.aborted)throw unavailable();
 const response=await fetcher('/api/workspace/',{credentials:'same-origin',cache:'no-store',redirect:'error',
  headers:{'X-Maipa-Agent-Actor':user},signal:AbortSignal.any([signal,AbortSignal.timeout(15000)])});
 if(response.status!==200||signal.aborted)throw unavailable();
 const value:unknown=await response.json();
 if(signal.aborted||!object(value)||!exact(value,['installation_id','role','permissions'])
  ||typeof value.installation_id!=='string'||!/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value.installation_id)
  ||!['administrator','preparer','reviewer','viewer'].includes(String(value.role))
  ||!Array.isArray(value.permissions)||value.permissions.length>100
  ||value.permissions.some(p=>typeof p!=='string'||p.length>100))throw unavailable();
 if(value.role==='reviewer'||value.role==='viewer')return [];
 if(!value.permissions.includes('agent.transport.check'))throw unavailable();
 return structuredClone(definitions.filter(v=>v.key!=='users'||value.role==='administrator'));
}

/** One mounted principal, one pending open, and the original connection/UI epoch. */
export class CommonViewsController {
 private closed=false;
 private pending=false;
 private lifetime=new AbortController();
 constructor(private user:string,private current:()=>ChatState,private committed:(ui:Composition)=>void,
  private opened:(view:CommonView,shared:boolean)=>void,private request:typeof fetch=fetch,
  private commit:typeof viewUiRequest=viewUiRequest){}
 async open(key:CommonViewKey):Promise<boolean>{
  if(this.closed)throw unavailable();
  if(this.pending)return false;
  this.pending=true;
  const captured=structuredClone({connection:this.current().connection,ui:this.current().ui});
  const signal=AbortSignal.any([this.lifetime.signal,AbortSignal.timeout(15000)]);
  const unchanged=()=>!this.closed&&!signal.aborted&&same(this.current().connection,captured.connection);
  try{
   const view=(await readCommonViews(this.user,signal,this.request)).find(v=>v.key===key);
   if(!view||!unchanged()||!same(this.current().ui,captured.ui))throw unavailable();
   if(!captured.ui){this.opened(view,false);return true;}
   const previous=captured.ui;
   const ack=await this.commit(this.user,previous,previous.composition?'maipa_ui_update':'maipa_ui_present',view.composition,signal);
   if(!object(ack)||!exact(ack,['id','revision','composition'])||ack.id!==previous.id||ack.revision!==previous.revision+1
    ||!same(ack.composition,view.composition)||!unchanged())throw unavailable();
   const current=this.current().ui;
   if(!current||current.id!==previous.id||current.account_id!==previous.account_id
    ||!(same(current,previous)||(current.revision===ack.revision&&same(current.composition,view.composition))))throw unavailable();
   this.committed({...previous,revision:Number(ack.revision),composition:view.composition});
   this.opened(view,true);return true;
  }catch{throw unavailable();}finally{this.pending=false;}
 }
 dispose(){this.closed=true;this.lifetime.abort();}
}
