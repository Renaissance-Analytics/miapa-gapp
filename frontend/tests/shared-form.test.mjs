import assert from 'node:assert/strict';
import test from 'node:test';
import {SharedFormController,SharedFormError,parseFormDraft,parseFormActivity,FormActivityCursor} from '../.test-build/shared-form.js';
const key='12345678-1234-4234-8234-123456789012',lease='12345678-1234-4234-8234-123456789013';
const binding={userId:'1',accountId:key,leaseId:lease,componentId:'shared-root'};
const draft=(revision=0,values={name:'',entity_name:''})=>({id:key,revision,component_id:'shared-root',kind:'new-client',fields:[
 {name:'name',label:'Client / engagement name',type:'text',required:true,max_length:128},
 {name:'entity_name',label:'Initial entity name',type:'text',required:true,max_length:128}],values});
const tick=()=>new Promise(resolve=>setImmediate(resolve));

test('held write acknowledgment retains newer typing and serializes the next exact revision',async()=>{
 let release;const commands=[];let calls=0;
 const controller=new SharedFormController(binding,()=>{},async(method,command)=>{
  if(method==='GET')return draft();commands.push(structuredClone(command));calls++;
  if(calls===1)return new Promise(resolve=>{release=()=>resolve(draft(1,{name:'First',entity_name:''}));});
  return draft(2,{name:'Newer Ω',entity_name:''});
 });
 await controller.load();controller.change('name','First');await tick();controller.change('name','Newer Ω');
 assert.equal(controller.state.values.name,'Newer Ω');assert.equal(controller.state.phase,'saving');
 release();assert.equal(await controller.flush(),true);assert.equal(controller.state.values.name,'Newer Ω');
 assert.equal(commands.length,2);assert.deepEqual(commands.map(command=>command.expected_revision),[0,1]);
 assert.equal(controller.state.saved.revision,2);
});

test('conflict retains local pending values and makes no automatic overwrite or retry',async()=>{
 let writes=0;const controller=new SharedFormController(binding,()=>{},async(method)=>{
  if(method==='GET')return draft(1,{name:'Human saved',entity_name:''});writes++;throw new SharedFormError('conflict');
 });
 await controller.load();controller.change('name','Local unsent');assert.equal(await controller.flush(),false);
 assert.equal(controller.state.phase,'conflict');assert.equal(controller.state.values.name,'Local unsent');
 await controller.load();assert.equal(controller.state.values.name,'Local unsent');assert.equal(writes,1);
 await controller.load(true);assert.equal(controller.state.values.name,'Human saved');assert.equal(controller.state.phase,'ready');
});

test('dispose fences a late response and clears the previous private form scope',async()=>{
 let release;const seen=[];const controller=new SharedFormController(binding,state=>seen.push(structuredClone(state)),async()=>new Promise(resolve=>{release=resolve;}));
 const loading=controller.load();await tick();controller.dispose();release(draft(4,{name:'Private late',entity_name:''}));await loading;
 assert.equal(controller.state.phase,'closed');assert.equal(controller.state.saved,null);assert.equal(controller.state.values.name,'');
 assert.ok(seen.every(state=>state.values.name!=='Private late'));
});

test('draft parser detaches exact values and refuses unknown fields wrong handles and invalid counters',()=>{
 const input=draft();input.values.name='  Exact Ω  ';const parsed=parseFormDraft(input,'shared-root');input.values.name='Caller mutated';
 assert.equal(parsed.values.name,'  Exact Ω  ');
 for(const bad of [{...draft(),private:'hidden'},{...draft(),component_id:'foreign'}, {...draft(),revision:true},
  {...draft(),values:{name:'x',entity_name:'y',password:'hidden'}}, {...draft(),values:{name:'x'.repeat(129),entity_name:''}}])assert.throws(()=>parseFormDraft(bad,'shared-root'));
});

test('only closed current-lease native receipts can become new activity and initial replay is ignored',()=>{
 const receipt={id:key,ui_id:lease,form_id:key,component_id:'shared-root',revision:1,action:'form_set_values',at_ms:123456789};
 assert.deepEqual(parseFormActivity(receipt,lease),receipt);
 for(const bad of [{...receipt,ui_id:key},{...receipt,values:{name:'private'}},{...receipt,action:'human_click'},{...receipt,revision:true}])assert.throws(()=>parseFormActivity(bad,lease));
 const cursor=new FormActivityCursor();assert.equal(cursor.accept(receipt,lease,true),false);assert.equal(cursor.accept(receipt,lease),false);
 const next={...receipt,id:lease,revision:2};assert.equal(cursor.accept(next,lease),true);assert.equal(cursor.accept(next,lease),false);
 assert.equal(cursor.accept(next,null),false);
});

test('clearing a submitted form cannot erase newer shared pending values',async()=>{
 let reads=0,writes=0;const controller=new SharedFormController(binding,()=>{},async(method)=>{
  if(method==='POST'){writes++;throw Error('Must not write');}
  return reads++===0?draft():draft(1,{name:'New native input',entity_name:''});
 });
 await controller.load();const submitted=structuredClone(controller.state.saved);await controller.load();
 assert.equal(await controller.clear(submitted),false);assert.equal(writes,0);assert.equal(controller.state.values.name,'New native input');
});

test('a changed authenticated actor clears private values instead of becoming a draft conflict',async()=>{
 const original=globalThis.fetch;let post;
 globalThis.fetch=async(path,options)=>{
  if(path==='/api/session/csrf/')return Response.json({csrf_token:'A'.repeat(64)});
  if(options.method==='GET')return Response.json(draft(1,{name:'Previous principal private input',entity_name:''}));
  post=JSON.parse(options.body);return Response.json({error:{code:'actor_changed'}},{status:409});
 };
 try{
  const controller=new SharedFormController(binding,()=>{});await controller.load();controller.change('entity_name','Unconfirmed input');
  assert.equal(await controller.flush(),false);assert.equal(post.expected_revision,1);
  assert.equal(controller.state.phase,'closed');assert.equal(controller.state.saved,null);assert.deepEqual(controller.state.values,{name:'',entity_name:''});
 }finally{globalThis.fetch=original;}
});
