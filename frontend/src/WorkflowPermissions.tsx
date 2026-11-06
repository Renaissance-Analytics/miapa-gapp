import {useEffect,useRef,useState} from 'react';
import {Button,Callout,Text} from '@particle-academy/react-fancy';
import {WorkflowPermissionController,type WorkflowConnection} from './workflow-permissions.js';
import type {WorkflowDraft} from './shared-workflow.js';

export function WorkflowPermissions({userId,draft,connection}:{userId:string;draft:WorkflowDraft;connection?:WorkflowConnection}){
 const [open,setOpen]=useState(false);
 return <section aria-label="Agent workflow permissions">
  <Button type="button" size="sm" variant="ghost" aria-expanded={open} onClick={()=>setOpen(v=>!v)}>Agent permissions</Button>
  {open&&(connection&&draft.entity_id?<PermissionControls key={`${userId}:${connection.accountId}:${connection.leaseId}:${draft.id}`}
   userId={userId} draft={draft} connection={connection} />:<Text size="sm">Connect the agent and open a workflow for a client to manage execution permissions.</Text>)}
 </section>;
}
function PermissionControls({userId,draft,connection}:{userId:string;draft:WorkflowDraft;connection:WorkflowConnection}){
 const [state,setState]=useState<WorkflowPermissionController['state']>({phase:'loading',value:null});
 const controller=useRef<WorkflowPermissionController|null>(null);
 useEffect(()=>{
  const current=new WorkflowPermissionController(userId,draft.id,connection,setState);controller.current=current;void current.load();
  const timer=setInterval(()=>{void current.load();},5000);
  return()=>{clearInterval(timer);current.dispose();controller.current=null;};
 },[userId,draft.id,connection.accountId,connection.leaseId]);
 useEffect(()=>{void controller.current?.load();},[draft.revision]);
 const value=state.value,pending=state.phase!=='ready';
 return <div aria-busy={state.phase==='loading'||state.phase==='pending'} data-workflow-agent-permission={value?.enabled?'enabled':'disabled'}>
  <Text size="sm">Only you can authorize this connection. Permission covers running, inspecting, resuming and cancelling workflows for {value?.entity_name??'this client'}.</Text>
  <Text size="sm">New permission lasts up to an hour. A new connection needs a new authorization. Human approvals and answers still require you.</Text>
  {state.phase==='loading'&&<Text size="sm" role="status">Checking permissions…</Text>}
  {value&&<Text size="sm" role="status">{value.enabled?'Agent execution allowed':'Agent execution not allowed'}{value.expires_at?` · connection permission ends ${new Date(value.expires_at).toLocaleString()}`:''}</Text>}
  {value&&!value.can_manage&&<Text size="sm">An administrator must authorize their own agent connection.</Text>}
  {value?.can_manage&&<Button id={`workflow-agent-permission-${draft.id}`} type="button" size="sm" disabled={pending||value.draft_revision!==draft.revision}
   onClick={()=>{void controller.current?.setEnabled(!value.enabled,draft.revision);}}>{value.enabled?'Revoke agent execution':'Allow agent execution for up to one hour'}</Button>}
  {state.phase==='pending'&&<Text size="sm" role="status">Waiting for permission confirmation…</Text>}
  {state.phase==='unavailable'&&<Callout color="amber" role="alert">Permissions changed or the result could not be confirmed. Refresh permissions before continuing.</Callout>}
  {state.phase==='closed'&&<Callout color="amber" role="status">This connection is unavailable or its authorization has expired. Reconnect the agent to continue.</Callout>}
  <Button type="button" size="sm" variant="ghost" disabled={state.phase==='pending'||state.phase==='closed'} onClick={()=>{void controller.current?.load();}}>Refresh permissions</Button>
 </div>;
}
