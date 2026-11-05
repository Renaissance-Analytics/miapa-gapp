import {useEffect,useRef,useState} from 'react';
import {Button,Callout,Text} from '@particle-academy/react-fancy';
import type {ChatState} from './agent-chat.js';
import {ModelPreferenceController,type PreferenceState} from './model-preference.js';

export function ModelPreference({userId,accountId,chat,busy}:{userId:string;accountId:string;chat:ChatState;busy:boolean}){
 const current=useRef(chat);current.current=chat;
 const controller=useRef<ModelPreferenceController|null>(null);
 const [state,setState]=useState<PreferenceState>({phase:'loading',value:null,latest:null,intent:null});
 useEffect(()=>{
  const c=new ModelPreferenceController(userId,accountId,()=>current.current,setState);controller.current=c;setState(c.state);void c.load();
  return()=>{c.close();if(controller.current===c)controller.current=null;};
 },[userId,accountId]);
 const pending=state.phase==='loading'||state.phase==='saving',ready=state.phase==='ready';
 const model=chat.models?.availableModels.find(m=>m.modelId===chat.models?.currentModelId);
 return <section className="model-preference" aria-label="Future connection model" data-model-preference={state.phase} data-model-preference-revision={state.value?.revision} data-model-preference-id={accountId} aria-busy={pending}>
  <Text size="sm" weight="medium">Future connections</Text>
  {state.value&&<Text size="sm" role="status">Saved model: {state.value.model_name??'Provider default'}</Text>}
  <div className="account-name-actions">
   <Button type="button" size="sm" disabled={!ready||busy||chat.phase!=='ready'||!model} onClick={()=>{void controller.current?.remember(chat);}}>Remember current model</Button>
   <Button type="button" size="sm" variant="ghost" disabled={!ready||busy} onClick={()=>{void controller.current?.useDefault();}}>Use provider default</Button>
   <Button type="button" size="sm" variant="ghost" disabled={pending} onClick={()=>{void controller.current?.load();}}>Reload saved model</Button>
  </div>
  <Text size="sm">The saved choice applies when you next connect. Your current connection stays as it is.</Text>
  {state.phase==='saving'&&<Text size="sm" role="status">Saving future model…</Text>}
  {state.intent&&<Text size="sm">Your choice: {state.intent.label}</Text>}
  {state.phase==='unavailable'&&<Callout color="amber" role="alert">We could not confirm the saved model. Reload it and review the result before trying again.</Callout>}
  {state.phase==='review'&&state.latest&&<Callout color="amber" role="alert"><Text size="sm">Saved model: {state.latest.model_name??'Provider default'}. Review this before making another change.</Text><Button type="button" size="sm" onClick={()=>controller.current?.acceptSaved()}>Use saved preference</Button></Callout>}
  {state.value?.issue&&<Callout color="amber" role="alert">{state.value.issue==='missing'?'The provider no longer offers your saved model.':'The provider did not confirm your saved model.'} Your choice is kept. Use provider default, then connect again, or retry connecting when the model is available.</Callout>}
 </section>;
}
