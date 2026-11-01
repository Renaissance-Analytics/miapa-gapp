import assert from 'node:assert/strict';
import test from 'node:test';
import {SharedWorkflowController,WorkflowError,parseWorkflowDraft,editorGraph,parseWorkflowActivity} from '../.test-build/shared-workflow.js';
import {compositionSchema} from '../.test-build/agent-chat.js';
const id='12345678-1234-4234-8234-123456789012';
const graph=label=>({nodes:[{id:'t',type:'@particle-academy/manual_trigger',position:{x:0,y:0},data:{kind:'@particle-academy/manual_trigger',label,config:{}}}],edges:[]});
const draft=(revision=0,label='Original')=>({id,owner_id:'1',name:'Shared workflow',entity_id:null,revision,graph:graph(label),graph_sha256:'a'.repeat(64),publication:null});
const tick=()=>new Promise(resolve=>setImmediate(resolve));

test('held graph acknowledgment preserves newer human edits and serializes exact revisions',async()=>{
 let release,writes=0;const commands=[];
 const c=new SharedWorkflowController('1',id,()=>{},async(action,command)=>{
  if(action==='read')return draft();commands.push(structuredClone(command));writes++;
  if(writes===1)return new Promise(resolve=>{release=()=>resolve(draft(1,'First'));});
  return draft(2,'Newer');
 });
 await c.load();c.change(graph('First'));await tick();c.change(graph('Newer'));release();
 assert.equal(await c.flush(),true);assert.equal(c.state.graph.nodes[0].data.label,'Newer');
 assert.deepEqual(commands.map(v=>v.expected_revision),[0,1]);assert.equal(c.state.saved.revision,2);
});

test('a native conflict preserves local graph until explicit reload and never overwrites remotely',async()=>{
 let writes=0;const c=new SharedWorkflowController('1',id,()=>{},async(action)=>{
  if(action==='read')return draft(4,'Native current');writes++;throw new WorkflowError('conflict');
 });
 await c.load();c.change(graph('Human unsent'));assert.equal(await c.flush(),false);
 await c.load();assert.equal(c.state.graph.nodes[0].data.label,'Human unsent');assert.equal(writes,1);
 await c.load(true);assert.equal(c.state.graph.nodes[0].data.label,'Native current');assert.equal(c.state.phase,'ready');
});

test('editor projection detaches semantic data and drops only documented canvas interaction state',()=>{
 const input=graph('Exact');input.nodes[0].selected=true;input.nodes[0].dragging=false;input.nodes[0].measured={width:100,height:80};
 const projected=editorGraph(input);assert.deepEqual(projected,graph('Exact'));input.nodes[0].data.label='Changed';
 assert.equal(projected.nodes[0].data.label,'Exact');
 assert.throws(()=>editorGraph({...graph('x'),nodes:[{...graph('x').nodes[0],data:{...graph('x').nodes[0].data,config:{x:NaN}}}]}));
 assert.throws(()=>editorGraph({...graph('x'),nodes:[{...graph('x').nodes[0],parentId:'hidden-lane'}]}));
});

test('actor changes and disposal fence late private graph responses and clear prior scope',async()=>{
 let release;const c=new SharedWorkflowController('1',id,()=>{},async()=>new Promise(resolve=>{release=resolve;}));
 const reading=c.load();await tick();c.dispose();release(draft(3,'Private late'));await reading;
 assert.equal(c.state.phase,'closed');assert.equal(c.state.saved,null);assert.equal(c.state.graph,null);
 const d=new SharedWorkflowController('1',id,()=>{},async action=>{if(action==='read')return draft();throw new WorkflowError('closed');});
 await d.load();d.change(graph('Private input'));assert.equal(await d.flush(),false);assert.equal(d.state.graph,null);
});

test('wrong principal draft handles and fabricated metadata are refused before rendering',()=>{
 const accepted=parseWorkflowDraft(draft(),id,'1');assert.equal(accepted.graph.nodes[0].id,'t');
 for(const bad of [{...draft(),owner_id:'2'},{...draft(),id:'foreign'},{...draft(),revision:true},{...draft(),authority:'allowed'}])
  assert.throws(()=>parseWorkflowDraft(bad,id,'1'));
 const schema={id:'workflow',type:'Maipa.Workflow',props:{draft_id:id}};
 assert.deepEqual(compositionSchema(schema),schema);
 assert.throws(()=>compositionSchema({...schema,props:{draft_id:id,graph:graph('Injected')}}));
});

test('workflow activity accepts only current lease value-free authoring receipts',()=>{
 const ui='12345678-1234-4234-8234-123456789013';
 const receipt={id,ui_id:ui,draft_id:id,component_id:'workflow',revision:4,action:'flow_update_node',at_ms:12345678};
 assert.deepEqual(parseWorkflowActivity(receipt,ui),receipt);
 for(const bad of [{...receipt,ui_id:id},{...receipt,graph:graph('Private')},{...receipt,action:'flow_run'},{...receipt,revision:true}])assert.throws(()=>parseWorkflowActivity(bad,ui));
});

test('controlled canvas retains local interaction state without persisting it or interrupting a drag',async()=>{
 let writes=0,reads=0,release,command;
 const c=new SharedWorkflowController('1',id,()=>{},async(action,value)=>{
  if(action==='read'){reads++;return draft();}
  writes++;command=structuredClone(value);return new Promise(resolve=>{release=()=>resolve({...draft(1),graph:command.graph});});
 });
 await c.load();const selected=graph('Original');Object.assign(selected.nodes[0],{selected:true,measured:{width:200,height:80}});
 c.change(selected);assert.equal(c.state.graph.nodes[0].selected,true);assert.equal(writes,0);
 await c.load();assert.equal(c.state.graph.nodes[0].selected,true);
 const moving=structuredClone(c.state.graph);moving.nodes[0].dragging=true;moving.nodes[0].position.x=40;c.change(moving);await tick();
 assert.equal(c.state.graph.nodes[0].dragging,true);assert.equal(writes,1);const readCount=reads;await c.load();assert.equal(reads,readCount);
 const dropped=structuredClone(c.state.graph);dropped.nodes[0].dragging=false;c.change(dropped);
 release();assert.equal(await c.flush(),true);assert.equal(writes,1);assert.equal(c.state.graph.nodes[0].selected,true);
 assert.equal(c.state.graph.nodes[0].dragging,false);assert.deepEqual(c.state.graph.nodes[0].measured,{width:200,height:80});
 assert.equal(c.state.saved.graph.nodes[0].position.x,40);
 assert.deepEqual(Object.keys(command.graph.nodes[0]).sort(),['data','id','position','type']);
});
