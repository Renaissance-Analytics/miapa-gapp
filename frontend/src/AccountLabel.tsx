import {useEffect,useRef,useState} from 'react';
import {Button,Callout,Form,Input,Text} from '@particle-academy/react-fancy';
import {AccountMetadataController,type AccountMetadata,type MetadataState} from './account-metadata.js';

export function AccountLabel({userId,accountId,saved}:{userId:string;accountId:string;saved:(value:AccountMetadata)=>void}){
 const controller=useRef<AccountMetadataController|null>(null),savedCallback=useRef(saved);savedCallback.current=saved;
 const [state,setState]=useState<MetadataState>({phase:'loading',value:null,latest:null,draft:''});
 useEffect(()=>{
  const current=new AccountMetadataController(userId,accountId,setState);controller.current=current;setState(current.state);void current.load();
  return()=>{current.close();if(controller.current===current)controller.current=null;};
 },[userId,accountId]);
 const pending=state.phase==='loading'||state.phase==='saving';
 const changed=!!state.value&&state.draft.trim()!==state.value.label&&!!state.draft.trim();
 return <Form className="account-name-form" aria-label="Account display name" data-account-metadata={state.phase}
  data-account-metadata-id={accountId} data-account-metadata-revision={state.value?.revision} aria-busy={pending}
  onSubmit={event=>{event.preventDefault();const current=controller.current;if(current)void current.save().then(ok=>{if(ok&&current.state.value)savedCallback.current(current.state.value);});}}>
  <Input id="settings-account-name" label="Account name" size="sm" maxLength={80} required value={state.draft}
   disabled={!state.value||state.phase==='loading'||state.phase==='closed'} onValueChange={value=>controller.current?.edit(value)}/>
  <div className="account-name-actions">
   <Button type="submit" size="sm" disabled={state.phase!=='ready'||!changed}>Save account name</Button>
   <Button type="button" size="sm" variant="ghost" disabled={pending} onClick={()=>{void controller.current?.load();}}>Reload saved name</Button>
  </div>
  {state.phase==='saving'&&<Text size="sm" role="status">Saving account name…</Text>}
  {state.phase==='ready'&&state.value&&<Text size="sm" role="status">Saved name: {state.value.label}</Text>}
  {state.phase==='unavailable'&&<Callout color="amber" role="alert">We could not confirm the account name. Your text is kept. Reload the saved name before trying again.</Callout>}
  {state.phase==='conflict'&&<Callout color="amber" role="alert">
   <Text size="sm">The saved name changed elsewhere. Your text is kept.</Text>
   {state.latest?<>
    <Text size="sm">Saved name: {state.latest.label}</Text>
    <div className="account-name-actions">
     <Button type="button" size="sm" onClick={()=>{const current=controller.current;current?.useSaved();if(current?.state.value)savedCallback.current(current.state.value);}}>Use saved name</Button>
     <Button type="button" size="sm" variant="ghost" onClick={()=>controller.current?.keepDraft()}>Keep my name</Button>
    </div>
   </>:<Text size="sm">Reload the saved name to review it before saving.</Text>}
  </Callout>}
 </Form>;
}
