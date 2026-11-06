import {useEffect,useMemo,useRef,useState} from 'react';
import {FlowRunControls,FlowRunFeed,type FlowRunFeedEntry} from '@particle-academy/fancy-flow';
import {Button,Callout,Checkbox,Input,Select,Text,Textarea} from '@particle-academy/react-fancy';
import {WorkflowExecutionController,type ExecutionState,type HumanGate} from './workflow-execution.js';
import type {WorkflowDraft} from './shared-workflow.js';

function HumanQuestion({gate,disabled,answer}:{gate:HumanGate;disabled:boolean;answer:(value:unknown)=>Promise<void>}){
 const [values,setValues]=useState<Record<string,string|boolean>>(()=>Object.fromEntries((gate.request.fields??[]).map(f=>[f.key,f.type==='checkbox'?false:''])));
 return <section aria-label="Workflow question" data-workflow-gate={gate.id}>
  <Text weight="semibold">{gate.request.title}</Text>
  <Text size="sm">Run version {gate.definition_revision} · {gate.node_id}</Text>
  {gate.request.description&&<Text>{gate.request.description}</Text>}
  <Textarea label="Input awaiting review" readOnly value={JSON.stringify(gate.input,null,2)} rows={3} />
  {gate.kind==='human_approval'?<div className="workflow-run-actions">
   <Button type="button" size="sm" disabled={disabled} onClick={()=>{void answer(true);}}>Approve</Button>
   <Button type="button" size="sm" variant="ghost" disabled={disabled} onClick={()=>{void answer(false);}}>Decline</Button>
  </div>:<form onSubmit={event=>{event.preventDefault();if(!disabled)void answer(values);}}>
   {(gate.request.fields??[]).map(field=>field.type==='checkbox'?<Checkbox key={field.key} id={`workflow-answer-${gate.id}-${field.key}`} label={field.label}
    checked={values[field.key]===true} disabled={disabled} onCheckedChange={value=>setValues(old=>({...old,[field.key]:value}))} />:
    field.type==='textarea'?<Textarea key={field.key} id={`workflow-answer-${gate.id}-${field.key}`} label={field.label} required={field.required} maxLength={2000}
     value={String(values[field.key])} disabled={disabled} onValueChange={value=>setValues(old=>({...old,[field.key]:value}))} />:
    <Input key={field.key} id={`workflow-answer-${gate.id}-${field.key}`} label={field.label} required={field.required} maxLength={2000}
     value={String(values[field.key])} disabled={disabled} onValueChange={value=>setValues(old=>({...old,[field.key]:value}))} />)}
   <Button type="submit" size="sm" disabled={disabled}>Submit answer</Button>
  </form>}
 </section>;
}

export function WorkflowExecution({userId,draft,flush}:{userId:string;draft:WorkflowDraft;flush:()=>Promise<boolean>}){
 const [state,setState]=useState<ExecutionState>({phase:'loading',snapshot:null,value:null});
 const controller=useRef<WorkflowExecutionController|null>(null),[seed,setSeed]=useState('{}'),[invalid,setInvalid]=useState(false);
 useEffect(()=>{
  const current=new WorkflowExecutionController(userId,draft.id,setState);controller.current=current;void current.load();
  const poll=setInterval(()=>{void current.load();},1500);
  return()=>{clearInterval(poll);current.dispose();controller.current=null;};
 },[userId,draft.id]);
 useEffect(()=>{void controller.current?.load();},[draft.revision,draft.publication?.definition_revision]);
 const current=state.value,run=current?.run,publication=draft.publication,selection=state.snapshot?.selection,showDetails=selection?.show_details??false;
 const observedAt=useMemo(()=>Date.now(),[run?.id,run?.step]);
 const pending=state.phase!=='ready',running=!!run&&['queued','running','waiting','blocked'].includes(run.status);
 const publishCurrent=!!publication&&publication.draft_revision===draft.revision;
 const ready=publishCurrent&&!!state.snapshot?.worker.available&&!pending;
 const feed:FlowRunFeedEntry[]=run?Object.entries(run.nodes).map(([node,row])=>({id:`${run.id}:${node}:${row.attempts}:${row.status}`,
  at:observedAt,level:row.status==='failed'?'error':'status',text:`${node}: ${row.status}${row.error?' · '+row.error.replaceAll('_',' '):''}`,nodeId:node})):[];
 async function start(){
  if(!ready||!publication||!await flush())return;
  let value:unknown;try{value=JSON.parse(seed);}catch{setInvalid(true);return;}
  if(!value||typeof value!=='object'||Array.isArray(value)){setInvalid(true);return;}setInvalid(false);
  await controller.current?.run(draft.revision,publication.definition_revision,value as Record<string,unknown>);
 }
 return <section className="workflow-execution" aria-label="Workflow execution" data-workflow-run={run?.id} data-workflow-run-status={run?.status} data-workflow-selection-revision={selection?.revision}>
  <div className="workflow-run-actions">
   {running?<fieldset disabled={pending}><legend className="sr-only">Run controls</legend><FlowRunControls running onRun={()=>{}} onCancel={()=>{void controller.current?.control('cancel');}} /></fieldset>:
    <fieldset disabled={!ready}><legend className="sr-only">Run controls</legend><FlowRunControls running={false} onRun={()=>{void start();}} /></fieldset>}
   <Text size="sm" role="status">{pending&&state.phase==='pending'?'Waiting for confirmation…':run?`${run.status.replaceAll('_',' ')} · version ${run.definition_revision}`:'No run yet'}</Text>
   <Button type="button" size="sm" variant="ghost" disabled={pending} aria-expanded={showDetails} onClick={()=>{void controller.current?.select(selection?.run_id??null,!showDetails);}}>Run details</Button>
   <Button type="button" size="sm" variant="ghost" disabled={state.phase==='pending'} onClick={()=>{void controller.current?.load();}}>Refresh runs</Button>
  </div>
  {!publishCurrent&&<Text size="sm">Publish the current graph before running it.</Text>}
  {state.snapshot&&!state.snapshot.worker.available&&<Callout color="amber" role="status">The workflow worker is unavailable. Saved runs remain available.</Callout>}
  {state.phase==='unavailable'&&<Callout color="amber" role="alert">We could not confirm the operation. Refresh runs before continuing. Keep the same input when retrying an unconfirmed run or answer.</Callout>}
  {state.phase==='closed'&&<Callout color="amber" role="alert">Run access is no longer available.</Callout>}
  {run&&publication&&run.definition_revision!==publication.definition_revision&&<Callout color="zinc" role="note">Viewing a run of version {run.definition_revision}. The editor contains the current draft.</Callout>}
  {run?.status==='blocked'&&<Button type="button" size="sm" disabled={pending||!state.snapshot?.worker.available} onClick={()=>{void controller.current?.control('resume');}}>Resume blocked run</Button>}
  {current?.gate&&<HumanQuestion key={current.gate.id} gate={current.gate} disabled={pending} answer={async value=>{await controller.current?.answer(value);}} />}
  {showDetails&&<div>
   {selection?.source==='agent'&&<Text size="sm" role="status" data-workflow-selection-source="agent">Maipa selected this run.</Text>}
   <Textarea id={`workflow-run-input-${draft.id}`} label="Run input (JSON object)" value={seed} disabled={state.phase==='pending'} onValueChange={setSeed} rows={3} maxLength={16000} />
   {invalid&&<Callout color="amber" role="alert">Enter a JSON object for this run.</Callout>}
   {!!state.snapshot?.runs.length&&<Select label="Saved run" value={run?.id??''} disabled={state.phase==='pending'}
    list={state.snapshot.runs.map(r=>({value:r.id,label:`Version ${r.definition_revision} · ${r.status} · ${r.id.slice(0,8)}`}))}
    onValueChange={id=>{void controller.current?.select(id,showDetails);}} />}
   {run&&<><Text size="sm">{run.error?run.error.replaceAll('_',' '):`Saved step ${run.step}`}</Text>
    <FlowRunFeed entries={feed} running={['queued','running'].includes(run.status)} title="Observed node state" />
    <Textarea label="Saved outputs" readOnly value={JSON.stringify(run.outputs,null,2)} rows={4} /></>}
  </div>}
 </section>;
}
