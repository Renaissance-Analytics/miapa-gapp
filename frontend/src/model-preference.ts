import type {ChatState} from './agent-chat.js';
import {csrfToken} from './api.js';
export interface ModelPreference {user_id:string;account_id:string;provider:string;revision:number;model_id:string|null;model_name:string|null;issue:'missing'|'unconfirmed'|null}
export interface PreferenceIntent {mode:'current_model'|'provider_default';modelId:string|null;label:string}
export interface PreferenceState {phase:'loading'|'ready'|'saving'|'review'|'unavailable'|'closed';value:ModelPreference|null;latest:ModelPreference|null;intent:PreferenceIntent|null}
const unavailable=()=>Error('Model preference could not be confirmed');
const uuid=(v:unknown):v is string=>typeof v==='string'&&/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(v);
const text=(v:unknown,max:number):v is string=>typeof v==='string'&&!!v.trim()&&v.length<=max&&!/[\u0000-\u001f\u007f]/.test(v);
export function parseModelPreference(v:unknown,user:string,account:string):ModelPreference{
 if(!v||typeof v!=='object'||Array.isArray(v))throw unavailable();
 const r=v as Record<string,unknown>;
 if(Object.keys(r).length!==7||!['user_id','account_id','provider','revision','model_id','model_name','issue'].every(k=>Object.hasOwn(r,k))
  ||!/^\d+$/.test(user)||!uuid(account)||r.user_id!==user||r.account_id!==account||typeof r.provider!=='string'||!/^[a-z][a-z0-9-]{0,63}$/.test(r.provider)
  ||!Number.isInteger(r.revision)||(r.revision as number)<0||(r.revision as number)>=2_000_000_000
  ||!((r.model_id===null&&r.model_name===null&&r.issue===null)||(text(r.model_id,200)&&r.model_id===r.model_id.trim()&&text(r.model_name,120)&&[null,'missing','unconfirmed'].includes(r.issue as null|string))))throw unavailable();
 return {...r} as unknown as ModelPreference;
}
function observed(s:ChatState,account:string){
 const model=s.models?.availableModels.find(m=>m.modelId===s.models?.currentModelId);
 return s.phase==='ready'&&s.connection.account_id===account&&uuid(s.id)&&model?{id:s.id,revision:s.revision,selection:s.connection.revision,provider:s.connection.provider,modelId:model.modelId,label:model.name}:null;
}
export class ModelPreferenceController {
 state:PreferenceState={phase:'loading',value:null,latest:null,intent:null};
 private lifetime=new AbortController();private pending=false;private path:string;
 constructor(private user:string,private account:string,private current:()=>ChatState,private changed:(state:PreferenceState)=>void,private request:typeof fetch=fetch){
  if(!/^\d+$/.test(user)||!uuid(account))throw unavailable();this.path=`/api/agent/accounts/${account}/model-preference/`;
 }
 private publish(state:PreferenceState){if(!this.lifetime.signal.aborted){this.state=state;this.changed(state);}}
 private options():RequestInit{return {credentials:'same-origin',cache:'no-store',redirect:'error',headers:{'X-Maipa-Agent-Actor':this.user},signal:AbortSignal.any([this.lifetime.signal,AbortSignal.timeout(10000)])};}
 async load():Promise<void>{
  if(this.pending||this.lifetime.signal.aborted)return;this.pending=true;this.publish({...this.state,phase:'loading',latest:null});
  try{
   const request=this.request,response=await request(this.path,this.options());if(response.status!==200)throw unavailable();
   const value=parseModelPreference(await response.json(),this.user,this.account),base=this.state.value;
   if(base&&(value.provider!==base.provider||value.revision<base.revision))throw unavailable();
   if(this.state.intent)this.publish({...this.state,phase:'review',latest:value});
   else this.publish({...this.state,phase:'ready',value,latest:null});
  }catch{this.publish({...this.state,phase:'unavailable'});}finally{this.pending=false;}
 }
 async remember(captured:ChatState):Promise<boolean>{
  const before=observed(captured,this.account),current=observed(this.current(),this.account);
  if(!before||JSON.stringify(before)!==JSON.stringify(current))return false;
  return this.save({mode:'current_model',modelId:before.modelId,label:before.label},before);
 }
 async useDefault():Promise<boolean>{return this.save({mode:'provider_default',modelId:null,label:'Provider default'},null);}
 private async save(intent:PreferenceIntent,context:ReturnType<typeof observed>):Promise<boolean>{
  if(this.pending||this.lifetime.signal.aborted||this.state.phase!=='ready'||!this.state.value)return false;
  const base=this.state.value;if(context&&context.provider!==base.provider)return false;
  this.pending=true;this.publish({...this.state,phase:'saving',intent,latest:null});
  try{
   const token=await csrfToken(this.request);
   if(this.lifetime.signal.aborted)return false;
   if(context&&JSON.stringify(observed(this.current(),this.account))!==JSON.stringify(context))throw unavailable();
   const body={mode:intent.mode,expected_revision:base.revision,...(context?{selection_revision:context.selection,conversation_revision:context.revision,conversation_id:context.id}:{})};
   const request=this.request,response=await request(this.path,{...this.options(),method:'POST',headers:{'Content-Type':'application/json','X-Maipa-Agent-Actor':this.user,'X-CSRFToken':token},body:JSON.stringify(body)});
   if(response.status!==200)throw unavailable();
   const value=parseModelPreference(await response.json(),this.user,this.account);
   const same=base.model_id===intent.modelId&&(intent.modelId===null||base.model_name===intent.label);
   if(value.provider!==base.provider||value.model_id!==intent.modelId||(intent.modelId!==null&&value.model_name!==intent.label)||value.revision!==base.revision+(same?0:1))throw unavailable();
   if(this.lifetime.signal.aborted)return false;
   this.publish({...this.state,phase:'ready',value,latest:null,intent:null});return true;
  }catch{this.publish({...this.state,phase:'unavailable'});return false;}finally{this.pending=false;}
 }
 acceptSaved():void{if(!this.pending&&this.state.phase==='review'&&this.state.latest)this.publish({...this.state,phase:'ready',value:this.state.latest,latest:null,intent:null});}
 close():void{if(!this.lifetime.signal.aborted){this.lifetime.abort();this.state={...this.state,phase:'closed'};}}
}
