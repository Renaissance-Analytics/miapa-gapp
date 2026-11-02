import assert from 'node:assert/strict';
import test from 'node:test';
import {WorkflowExecutionController,parseExecution} from '../.test-build/workflow-execution.js';
import {SharedWorkflowController} from '../.test-build/shared-workflow.js';
const draft='12345678-1234-4234-8234-123456789012',definition='22345678-1234-4234-8234-123456789012',runId='32345678-1234-4234-8234-123456789012';
const publication={status:'published',draft_id:draft,draft_revision:2,definition_id:definition,definition_revision:1,schema_sha256:'a'.repeat(64)};
const run=(status='queued',step=0)=>({id:runId,definition_id:definition,definition_revision:1,schema_sha256:'a'.repeat(64),runtime:'0.30.1',status,step,error:null,outputs:{},nodes:{}});
const snapshot=(runs=[],worker=true)=>({draft_id:draft,draft_revision:2,publication,worker:{available:worker,state:worker?'running':'not_running'},runs,selection:{revision:0,run_id:runs[0]?.id??null,show_details:false,source:'default'}});
const value=(status='queued',step=0)=>({run:run(status,step),worker:{available:true,state:'running'},gate:null,selection:{revision:1,run_id:runId,show_details:false,source:'human'}});

test('acknowledged dispatch preserves its immutable version and reports server state only',async()=>{
 const calls=[];const c=new WorkflowExecutionController('1',draft,()=>{},async(action,command)=>{
  calls.push({action,command});return action==='snapshot'?snapshot():value();
 });
 await c.load();await c.run(2,1,{name:'Ada'});
 assert.equal(c.state.value.run.status,'queued');assert.deepEqual(c.state.value.run.outputs,{});
 assert.equal(calls[1].command.expected_revision,2);assert.equal(calls[1].command.definition_revision,1);
 assert.deepEqual(calls[1].command.seed,{name:'Ada'});assert.match(calls[1].command.request_key,/^[0-9a-f-]{36}$/);
});

test('unconfirmed dispatch retries retain the same request identity and seed',async()=>{
 const calls=[];let fail=true;const c=new WorkflowExecutionController('1',draft,()=>{},async(action,command)=>{
  if(action==='snapshot')return snapshot();calls.push(structuredClone(command));if(fail){fail=false;throw Error('lost receipt');}return value();
 });
 await c.load();await c.run(2,1,{name:'Ada'});assert.equal(c.state.phase,'unavailable');
 await c.load();await c.run(2,1,{name:'Changed'});assert.equal(calls.length,1);
 await c.load();await c.run(2,1,{name:'Ada'});assert.equal(calls.length,2);
 assert.deepEqual(calls[0],calls[1]);assert.equal(c.state.value.run.status,'queued');
});

test('stopped worker or stale draft cannot dispatch and cancellation uses exact observed step',async()=>{
 const calls=[];let ready=false;const c=new WorkflowExecutionController('1',draft,()=>{},async(action,command)=>{
  calls.push({action,command});return action==='snapshot'?snapshot([run('waiting',4)],ready):action==='inspect'?value('waiting',4):value('cancelled',5);
 });
 await c.load();await c.run(2,1,{});assert.equal(calls.filter(x=>x.action==='run').length,0);
 ready=true;await c.load();await c.run(3,1,{});assert.equal(calls.filter(x=>x.action==='run').length,0);
 await c.load();await c.control('cancel');assert.deepEqual(calls.find(x=>x.action==='cancel').command,{run_id:runId,expected_step:4});
 assert.equal(c.state.value.run.status,'cancelled');
});

test('dispose fences a late accepted response without cancelling actual backend work',async()=>{
 const calls=[];let release;const c=new WorkflowExecutionController('1',draft,()=>{},async(action)=>{
  calls.push(action);return action==='snapshot'?snapshot():new Promise(resolve=>{release=()=>resolve(value());});
 });
 await c.load();const pending=c.run(2,1,{});await new Promise(resolve=>setImmediate(resolve));c.dispose();release();await pending;
 assert.equal(c.state.phase,'closed');assert.equal(c.state.value,null);assert.deepEqual(calls,['snapshot','run']);
});

test('foreign definition and forged gate versions are refused before showing run evidence',()=>{
 const foreign=snapshot([run()]);foreign.runs[0].definition_id='42345678-1234-4234-8234-123456789012';
 assert.throws(()=>parseExecution(foreign,draft));
 const bad=value('waiting',3);bad.gate={id:'52345678-1234-4234-8234-123456789012',run_id:runId,node_id:'ask',kind:'human_approval',step:2,
  definition_revision:1,schema_sha256:'a'.repeat(64),request_sha256:'b'.repeat(64),request:{title:'Approval',description:null},input:{}};
 assert.throws(()=>parseExecution(bad,draft,definition));
});

test('human publication acknowledges its exact saved graph without creating a run',async()=>{
 const graph={nodes:[],edges:[]};const saved={id:draft,owner_id:'1',name:'Workflow',entity_id:null,revision:2,graph,graph_sha256:'c'.repeat(64),publication:null};
 const calls=[];let release;const c=new SharedWorkflowController('1',draft,()=>{},async(action,command)=>{
  calls.push({action,command});return action==='read'?saved:new Promise(resolve=>{release=()=>resolve(publication);});
 });
 await c.load();const pending=c.publishVersion();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(c.state.phase,'publishing');assert.deepEqual(calls[1],{action:'publish',command:{expected_revision:2}});
 release();await pending;assert.equal(c.state.phase,'ready');assert.deepEqual(c.state.saved.publication,publication);
 assert.deepEqual(calls.map(c=>c.action),['read','publish']);
});

test('unconfirmed human answers retain the exact gate and retry identity without borrowing seeded values',async()=>{
 let accepted=false,fail=true;const calls=[];const waiting=value('waiting',3);
 waiting.gate={id:'52345678-1234-4234-8234-123456789012',run_id:runId,node_id:'ask',kind:'user_input',step:3,
  definition_revision:1,schema_sha256:'a'.repeat(64),request_sha256:'b'.repeat(64),request:{title:'Your input',fields:[{key:'answer',label:'Answer',type:'text',required:true}]},input:{in:{values:{answer:'Seeded'}}}};
 const c=new WorkflowExecutionController('1',draft,()=>{},async(action,command)=>{
  if(action==='snapshot')return snapshot([run(accepted?'completed':'waiting',accepted?5:3)]);
  if(action==='inspect')return accepted?value('completed',5):waiting;
  calls.push(structuredClone(command));if(fail){fail=false;throw Error('lost answer receipt');}
  accepted=true;return {status:'recorded',id:'62345678-1234-4234-8234-123456789012',run_id:runId,gate_id:waiting.gate.id,accepted_step:3,next_step:4};
 });
 await c.load();await c.answer({answer:'Human'});assert.equal(c.state.phase,'unavailable');
 assert.equal(c.state.value.gate.id,waiting.gate.id);assert.deepEqual(calls[0].answer,{answer:'Human'});
 await c.load();await c.answer({answer:'Changed'});assert.equal(calls.length,1);
 await c.load();await c.answer({answer:'Human'});assert.equal(calls.length,2);assert.deepEqual(calls[0],calls[1]);
 assert.equal(c.state.value.run.status,'completed');assert.equal(c.state.value.gate,null);
});

test('actor replacement closes execution state instead of retaining the previous human question',async()=>{
 const previous=globalThis.fetch;globalThis.fetch=async()=>new Response(JSON.stringify({error:{code:'actor_changed'}}),{status:409});
 try{const c=new WorkflowExecutionController('1',draft,()=>{});await c.load();assert.equal(c.state.phase,'closed');assert.equal(c.state.value,null);assert.equal(c.state.snapshot,null);}
 finally{globalThis.fetch=previous;}
});

test('an inspection receipt for a different run in the same workflow is refused',async()=>{
 const other=value();other.run.id='72345678-1234-4234-8234-123456789012';
 const c=new WorkflowExecutionController('1',draft,()=>{},async action=>action==='snapshot'?snapshot([run()]):other);
 await c.load();assert.equal(c.state.phase,'unavailable');assert.equal(c.state.value,null);
});

test('a human cancellation waits for an in-flight status read instead of losing the click',async()=>{
 let reads=0,release;const calls=[];
 const c=new WorkflowExecutionController('1',draft,()=>{},async(action,command)=>{
  if(action==='snapshot')return snapshot([run('waiting',3)]);
  if(action==='inspect'){if(++reads===2)return new Promise(resolve=>{release=()=>resolve(value('waiting',3));});return value('waiting',3);}
  calls.push(command);return value('cancelled',4);
 });
 await c.load();const reading=c.load();await new Promise(resolve=>setImmediate(resolve));
 const cancelled=c.control('cancel');release();await Promise.all([reading,cancelled]);
 assert.deepEqual(calls,[{run_id:runId,expected_step:3}]);assert.equal(c.state.value.run.status,'cancelled');
});
