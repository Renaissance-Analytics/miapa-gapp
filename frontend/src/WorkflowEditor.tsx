import {useEffect,useRef,useState} from 'react';
import {FlowEditor,type FlowEditorApi,type FlowEditorProps} from '@particle-academy/fancy-flow';
import '@particle-academy/fancy-flow/styles.css';
import {reactFancyFieldRenderers} from '@particle-academy/fancy-flow/fields/react-fancy';
import {Button,Callout,Text,useTheme} from '@particle-academy/react-fancy';
import {SharedWorkflowController,type WorkflowState,type WorkflowActivity} from './shared-workflow.js';
import {WorkflowExecution} from './WorkflowExecution.js';

const supported=new Set(['manual_trigger','transform','output','human_approval','user_input'].map(k=>'@particle-academy/'+k));
const kindFilter:FlowEditorProps['kindFilter']=({kind})=>supported.has(kind.name);
export function WorkflowEditor({userId,draftId,activity}:{userId:string;draftId:string;activity?:WorkflowActivity}){
 const theme=useTheme();
 const [state,setState]=useState<WorkflowState>({phase:'loading',saved:null,graph:null});
 const controller=useRef<SharedWorkflowController|null>(null),container=useRef<HTMLDivElement>(null);
 const [height,setHeight]=useState(420),[width,setWidth]=useState(0),[selected,setSelected]=useState(false),[showKinds,setShowKinds]=useState(false);
 const editor=useRef<FlowEditorApi>(null);
 useEffect(()=>{
  const current=new SharedWorkflowController(userId,draftId,setState);controller.current=current;setState(current.state);void current.load();
  const poll=setInterval(()=>{void current.load();},1500);
  return()=>{clearInterval(poll);current.dispose();controller.current=null;};
 },[userId,draftId]);
 useEffect(()=>{void controller.current?.load();},[activity?.id]);
 useEffect(()=>{
  const measure=()=>{if(container.current){const bounds=container.current.getBoundingClientRect();setHeight(Math.max(240,Math.min(640,innerHeight-bounds.top-34)));setWidth(bounds.width);}};
  measure();const observer=new ResizeObserver(measure);if(container.current)observer.observe(container.current);window.addEventListener('resize',measure);
  return()=>{observer.disconnect();window.removeEventListener('resize',measure);};
 },[]);
 useEffect(()=>{const frame=requestAnimationFrame(()=>editor.current?.fitView());return()=>cancelAnimationFrame(frame);},[state.graph?.nodes.length,width,showKinds,selected]);
 const editable=['ready','saving'].includes(state.phase);
 return <section className="workflow-workspace" aria-label="Shared workflow" data-workflow-draft={state.saved?.id}
  data-workflow-revision={state.saved?.revision} data-workflow-phase={state.phase}>
  <header><Text weight="semibold">{state.saved?.name??'Workflow'}</Text>
   <Text size="sm" role="status">{state.phase==='saving'?'Saving…':state.phase==='publishing'?'Publishing…':state.phase==='ready'?`Saved · revision ${state.saved?.revision}`:state.phase==='loading'?'Loading…':''}</Text>
   {state.saved&&state.saved.publication?.draft_revision!==state.saved.revision&&<Button type="button" size="sm" disabled={state.phase!=='ready'}
    onClick={()=>{void controller.current?.publishVersion();}}>Publish version</Button>}</header>
  {['conflict','unavailable'].includes(state.phase)&&<Callout color="amber" role="alert">
   {state.phase==='conflict'?'The workflow changed. Your unsaved graph is kept here.':'We could not confirm the saved workflow. Your graph is kept here.'}
   <Button size="sm" variant="ghost" onClick={()=>{void controller.current?.load(true);}}>Reload saved workflow</Button></Callout>}
  {state.phase==='closed'&&<Callout color="amber" role="status">This workflow is no longer available.</Callout>}
  {activity&&activity.draft_id===state.saved?.id&&activity.revision===state.saved.revision&&<Callout color="blue" role="status" data-native-workflow-activity={activity.id}>Maipa updated this workflow.</Callout>}
  {state.saved&&<WorkflowExecution userId={userId} draft={state.saved} flush={async()=>await controller.current?.flush()??false} />}
  <div ref={container}>{state.graph&&<fieldset disabled={!editable}><legend className="sr-only">Edit workflow</legend>
   <FlowEditor ref={editor} value={state.graph} onChange={graph=>controller.current?.change(graph)} height={height}
    className={theme.resolved==='light'?'ff-canvas--light':'dark'}
    showPalette={width>=900||showKinds&&!selected} showPanel={selected} onSelectionChange={node=>setSelected(!!node)}
    actions={[{id:'maipa-node-palette',label:showKinds?'Hide nodes':'Add node',placement:'start',disabled:()=>!editable,
     onSelect:api=>{api.select(null);setShowKinds(value=>!value);}}]}
    kindFilter={kindFilter} fieldRenderers={reactFancyFieldRenderers} showFeed={false}
    builtins={{run:false,addLane:false,import:false,export:false}}
    canvasProps={{nodesDraggable:editable,nodesConnectable:editable,elementsSelectable:editable,showMinimap:false,minZoom:0.1,colorMode:theme.resolved}} />
  </fieldset>}</div>
 </section>;
}
