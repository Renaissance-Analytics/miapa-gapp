import test from 'node:test';
import assert from 'node:assert/strict';
import {WorkflowExecutionController,parseExecution} from '../.test-build/workflow-execution.js';
const draft='12345678-1234-4234-8234-123456789012',definition='22345678-1234-4234-8234-123456789012';
const a='32345678-1234-4234-8234-123456789012',b='42345678-1234-4234-8234-123456789012';
const publication={status:'published',draft_id:draft,draft_revision:2,definition_id:definition,definition_revision:1,schema_sha256:'a'.repeat(64)};
const run=id=>({id,definition_id:definition,definition_revision:1,schema_sha256:'a'.repeat(64),runtime:'0.30.1',step:3,status:'completed',error:null,outputs:{result:id},nodes:{}});
const selection=(run_id=a,revision=1,show_details=true,source='human')=>({run_id,revision,show_details,source});
const snapshot=s=>({draft_id:draft,draft_revision:2,publication,worker:{available:true,state:'running'},runs:[run(a),run(b)],selection:s});
const detail=id=>({run:run(id),worker:{available:true,state:'running'},gate:null});

test('polling follows the actual shared selected run and details instead of a private preference',async()=>{
 let saved=selection();const c=new WorkflowExecutionController('1',draft,()=>{},async(action,cmd)=>action==='snapshot'?snapshot(saved):detail(cmd.run_id));
 await c.load();assert.equal(c.state.value.run.id,a);saved=selection(b,2,false,'agent');await c.load();
 assert.equal(c.state.value.run.id,b);assert.equal(c.state.snapshot.selection.show_details,false);assert.equal(c.state.snapshot.selection.source,'agent');
 const reloaded=new WorkflowExecutionController('1',draft,()=>{},async(action,cmd)=>action==='snapshot'?snapshot(saved):detail(cmd.run_id));
 await reloaded.load();assert.equal(reloaded.state.value.run.id,b);
});

test('human choice awaits its exact revisioned acknowledgment and then reads the real selected output',async()=>{
 let saved=selection(),release;const calls=[];const c=new WorkflowExecutionController('1',draft,()=>{},async(action,cmd)=>{
  if(action==='snapshot')return snapshot(saved);if(action==='inspect')return detail(cmd.run_id);calls.push(cmd);
  return new Promise(resolve=>{release=()=>{saved=selection(b,2,false);resolve(saved);};});
 });
 await c.load();const pending=c.select(b,false);await new Promise(r=>setImmediate(r));
 assert.equal(c.state.phase,'pending');assert.equal(c.state.value.run.id,a);assert.deepEqual(calls,[{expected_revision:1,run_id:b,show_details:false}]);
 release();await pending;assert.equal(c.state.value.run.id,b);assert.equal(c.state.snapshot.selection.revision,2);
});

test('foreign or malformed shared selection is rejected before any private run inspection',async()=>{
 for(const s of [{...selection(),run_id:'52345678-1234-4234-8234-123456789012'},{...selection(),source:'operator'},{...selection(),show_details:'yes'},{...selection(),revision:-1}])assert.throws(()=>parseExecution(snapshot(s),draft));
});

test('a stale or ambiguous navigation write requires readback and never retries over a newer agent choice',async()=>{
 let saved=selection(),writes=0;const c=new WorkflowExecutionController('1',draft,()=>{},async(action,cmd)=>{
  if(action==='snapshot')return snapshot(saved);if(action==='inspect')return detail(cmd.run_id);
  writes++;saved=selection(b,2,false,'agent');throw Error('Conflict or missing receipt');
 });
 await c.load();await c.select(b,true);assert.equal(c.state.phase,'unavailable');await c.select(a,true);assert.equal(writes,1);
 await c.load();assert.equal(c.state.value.run.id,b);assert.equal(c.state.snapshot.selection.show_details,false);
});

test('a wrong selection receipt and late disposal cannot replace current run evidence',async()=>{
 let release;const c=new WorkflowExecutionController('1',draft,()=>{},async(action,cmd)=>action==='snapshot'?snapshot(selection()):action==='inspect'?detail(cmd.run_id):new Promise(resolve=>{release=()=>resolve(selection(a,2,true));}));
 await c.load();let pending=c.select(b,true);await new Promise(r=>setImmediate(r));release();await pending;
 assert.equal(c.state.phase,'unavailable');assert.equal(c.state.value.run.id,a);
 await c.load();pending=c.select(b,true);await new Promise(r=>setImmediate(r));c.dispose();release();await pending;
 assert.equal(c.state.phase,'closed');assert.equal(c.state.value,null);
});
