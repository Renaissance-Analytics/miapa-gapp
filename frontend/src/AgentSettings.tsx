import {useEffect,useState} from 'react';
import {Button,Callout,Heading,Select,Text} from '@particle-academy/react-fancy';
import {readSettings,type AgentSettingsSnapshot,type SettingsControls} from './agent-settings.js';
import {AccountLabel} from './AccountLabel.js';

export function AgentSettings({userId,controls}:{userId:string;controls:SettingsControls|null}){
 const [metadata,setMetadata]=useState<AgentSettingsSnapshot>();
 const [phase,setPhase]=useState<'loading'|'ready'|'unavailable'>('loading'),[reload,setReload]=useState(0);
 useEffect(()=>{
  const abort=new AbortController();setPhase('loading');setMetadata(undefined);
  void readSettings(userId,abort.signal).then(value=>{if(!abort.signal.aborted){setMetadata(value);setPhase('ready');}})
   .catch(()=>{if(!abort.signal.aborted)setPhase('unavailable');});
  return()=>abort.abort();
 },[userId,reload,controls?.state.connection.account_id]);
 const s=controls?.state,ready=phase==='ready'&&!!controls&&!controls.busy;
 const canSwitch=ready&&!!s&&['offline','unavailable'].includes(s.phase);
 const account=metadata?.accounts.find(a=>a.id===s?.connection.account_id);
 return <section className="agent-settings" aria-label="Agent settings" data-agent-settings={phase} aria-busy={phase==='loading'||controls?.busy}>
  <div className="intake-heading"><Heading as="h3" size="md">Agent settings</Heading><Button size="sm" variant="ghost" disabled={phase==='loading'} onClick={()=>setReload(n=>n+1)}>Reload agent settings</Button></div>
  {phase==='loading'&&<Text role="status" size="sm">Loading your accounts…</Text>}
  {phase==='unavailable'&&<Callout role="alert" color="amber">Your agent settings could not be confirmed. Reload before making a change.</Callout>}
  {metadata&&s&&<>
   <Select id="settings-provider" label="Provider" size="sm" value={s.connection.provider??''} placeholder="Choose provider" disabled={!canSwitch}
    list={metadata.providers.map(p=>({value:p.id,label:p.available?p.label:p.label+' · unavailable',disabled:!p.available}))}
    onValueChange={value=>{void controls?.act(s,'provider',value);}} />
   <Text size="sm" role="status">{s.phase==='ready'?'Connected':s.phase==='thinking'?'Agent is working':s.phase==='sign_in'?'Sign-in needed in chat':s.phase==='connecting'?'Connecting':s.phase==='stopping'?'Stopping':'Not connected'}</Text>
   {account&&<Text size="sm">{account.label} · {account.profile_assigned?'Private app profile assigned':'App profile created when needed'}</Text>}
   {s.models?<Select id="settings-model" label="Model for this connection" size="sm" value={s.models.currentModelId??''} placeholder="Provider default"
    list={s.models.availableModels.map(m=>({value:m.modelId,label:m.name}))} disabled={!ready||s.phase!=='ready'}
    onValueChange={value=>{void controls?.act(s,'model',value);}} />:<Text size="sm">Model choices appear when this provider exposes them on a connected session.</Text>}
   <Text size="sm">Model changes apply to this live connection. Existing machine sign-ins are reused; complete any requested sign-in in chat.</Text>
   {canSwitch&&s.connection.available&&<Button size="sm" onClick={()=>{void controls?.act(s,'connect');}}>Connect selected provider</Button>}
   {s.phase==='ready'&&<Button size="sm" variant="ghost" disabled={!ready} onClick={()=>{void controls?.act(s,'disconnect');}}>Disconnect selected provider</Button>}
   {!!metadata.accounts.length&&<Text size="sm">Owned accounts: {metadata.accounts.map(a=>`${a.label} (${metadata.providers.find(p=>p.id===a.provider)?.label??a.provider})`).join(', ')}</Text>}
  </>}
  {s?.connection.account_id&&<AccountLabel key={`${userId}:${s.connection.account_id}`} userId={userId} accountId={s.connection.account_id}
   saved={value=>setMetadata(previous=>previous?{...previous,accounts:previous.accounts.map(row=>row.id===value.account_id?{...row,label:value.label}:row)}:previous)}/>}
  {controls?.error&&<Callout role="alert" color="amber">{controls.error}</Callout>}
 </section>;
}
