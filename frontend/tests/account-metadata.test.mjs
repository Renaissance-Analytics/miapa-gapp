import test from 'node:test';
import assert from 'node:assert/strict';
import {parseAccountMetadata,readAccountMetadata,saveAccountMetadata,AccountMetadataController} from '../.test-build/account-metadata.js';

const id='12345678-1234-4234-8234-123456789012';
const row=(label='Original',revision=0)=>({user_id:'1',account_id:id,provider:'claude',label,profile_assigned:true,revision});
const response=value=>Response.json(value);
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));

test('account metadata rejects foreign identities extra authority and invalid revisions',()=>{
 assert.deepEqual(parseAccountMetadata(row(),'1',id),row());
 for(const value of [{...row(),user_id:'2'},{...row(),account_id:'foreign'}, {...row(),token:'private'},
  {...row(),enabled:true},{...row(),revision:true},{...row(),revision:-1},{...row(),revision:2_000_000_000},
  {...row(),label:'x'.repeat(81)},{...row(),label:' padded '},{...row(),label:'line\nbreak'}]){
  assert.throws(()=>parseAccountMetadata(value,'1',id));
 }
});

test('metadata transport binds the owned account actor csrf and exact observed revision',async()=>{
 const calls=[],signal=new AbortController().signal;
 const request=async(url,options)=>{calls.push({url,options});return response(url==='/api/session/csrf/'?{csrf_token:'a'.repeat(64)}:options.method==='POST'?row('Accepted',1):row());};
 assert.deepEqual(await readAccountMetadata('1',id,signal,request),row());
 assert.deepEqual(await saveAccountMetadata('1',row(),'Accepted',signal,request),row('Accepted',1));
 const save=calls.find(c=>c.options.method==='POST');assert.equal(save.url,`/api/agent/accounts/${id}/metadata/`);
 assert.deepEqual(JSON.parse(save.options.body),{expected_revision:0,label:'Accepted'});
 assert.equal(save.options.headers['X-Maipa-Agent-Actor'],'1');assert.equal(save.options.headers['X-CSRFToken'],'a'.repeat(64));
 for(const call of calls.filter(c=>c.url!=='/api/session/csrf/')){
  assert.equal(call.options.credentials,'same-origin');assert.equal(call.options.cache,'no-store');assert.equal(call.options.redirect,'error');assert.ok(call.options.signal);
 }
 await assert.rejects(()=>saveAccountMetadata('1',row(),'Valid',signal,async(url)=>response(url==='/api/session/csrf/'?{csrf_token:'a'.repeat(64)}:row('Wrong',1))));
});

test('one pending rename preserves edits made before its exact acknowledgment arrives',async()=>{
 let resolveSave;const posts=[];
 const request=async(url,options)=>url==='/api/session/csrf/'?response({csrf_token:'a'.repeat(64)}):options.method==='POST'?
  (posts.push(JSON.parse(options.body)),await new Promise(resolve=>{resolveSave=resolve;})):response(row());
 const changes=[],controller=new AccountMetadataController('1',id,value=>changes.push(structuredClone(value)),request);
 await controller.load();controller.edit('Submitted');const saving=controller.save();await tick();
 assert.equal(posts.length,1);controller.edit('Newer unsent name');assert.equal(await controller.save(),false);
 resolveSave(response(row('Submitted',1)));assert.equal(await saving,true);
 assert.equal(controller.state.value.label,'Submitted');assert.equal(controller.state.draft,'Newer unsent name');
 assert.equal(controller.state.phase,'ready');assert.equal(posts.length,1);assert.equal(changes.at(-1).draft,'Newer unsent name');controller.close();
});

test('conflict readback keeps local text until explicit review before another save',async()=>{
 let current=row(),writes=0;const posts=[];
 const request=async(url,options)=>url==='/api/session/csrf/'?response({csrf_token:'a'.repeat(64)}):options.method==='POST'?
  (posts.push(JSON.parse(options.body)),++writes===1?new Response('',{status:409}):response(current=row('Local proposal',2))):response(current);
 const controller=new AccountMetadataController('1',id,()=>{},request);await controller.load();controller.edit('Local proposal');
 assert.equal(await controller.save(),false);assert.equal(controller.state.phase,'conflict');assert.equal(controller.state.draft,'Local proposal');
 assert.equal(await controller.save(),false);current=row('Other tab',1);await controller.load();
 assert.equal(controller.state.draft,'Local proposal');assert.equal(controller.state.latest.label,'Other tab');
 assert.equal(controller.state.phase,'conflict');assert.equal(await controller.save(),false);assert.equal(posts.length,1);
 controller.keepDraft();assert.equal(controller.state.value.revision,1);assert.equal(await controller.save(),true);
 assert.deepEqual(posts[1],{expected_revision:1,label:'Local proposal'});assert.equal(controller.state.phase,'ready');controller.close();
});

test('lost or malformed save acknowledgments retain input and never automatically retry',async()=>{
 let current=row(),posts=0;
 const request=async(url,options)=>{
  if(url==='/api/session/csrf/')return response({csrf_token:'a'.repeat(64)});
  if(options.method==='POST'){posts++;current=row('Desired',1);throw Error('Synthetic lost acknowledgment');}
  return response(current);
 };
 const controller=new AccountMetadataController('1',id,()=>{},request);await controller.load();controller.edit('Desired');
 assert.equal(await controller.save(),false);assert.equal(controller.state.phase,'unavailable');assert.equal(controller.state.draft,'Desired');
 assert.equal(await controller.save(),false);await controller.load();assert.equal(posts,1);
 assert.equal(controller.state.phase,'conflict');assert.equal(controller.state.latest.label,'Desired');controller.useSaved();
 assert.equal(controller.state.phase,'ready');assert.equal(controller.state.value.revision,1);assert.equal(controller.state.draft,'Desired');controller.close();
 for(const ack of [row('Different',1),{...row('Desired',1),user_id:'2'},row('Desired',8)]){
  let changed=0;const c=new AccountMetadataController('1',id,()=>{},async(url,o)=>url==='/api/session/csrf/'?response({csrf_token:'a'.repeat(64)}):o.method==='POST'?(changed++,response(ack)):response(row()));
  await c.load();c.edit('Desired');assert.equal(await c.save(),false);assert.equal(c.state.draft,'Desired');assert.equal(c.state.value.revision,0);assert.equal(c.state.phase,'unavailable');assert.equal(changed,1);c.close();
 }
});

test('closing a principal during delayed reads or csrf bootstrap cannot publish or send a write',async()=>{
 let resolveRead;const changes=[];const controller=new AccountMetadataController('1',id,v=>changes.push(v),async()=>new Promise(r=>{resolveRead=r;}));
 const reading=controller.load();assert.equal(typeof resolveRead,'function');controller.close();const before=changes.length;resolveRead(response(row()));await reading;
 assert.equal(controller.state.phase,'closed');assert.equal(changes.length,before);
 let resolveCsrf,writes=0;const second=new AccountMetadataController('1',id,()=>{},async(url,o)=>url==='/api/session/csrf/'?new Promise(r=>{resolveCsrf=r;}):o.method==='POST'?(writes++,response(row('Delayed',1))):response(row()));
 await second.load();second.edit('Delayed');const saving=second.save();await tick();second.close();resolveCsrf(response({csrf_token:'a'.repeat(64)}));
 assert.equal(await saving,false);assert.equal(writes,0);assert.equal(second.state.phase,'closed');
});

test('explicit reload follows a changed saved name without replacing newer local input',async()=>{
 let current=row(),delayed=false,resolveRead;
 const controller=new AccountMetadataController('1',id,()=>{},async()=>delayed?new Promise(resolve=>{resolveRead=resolve;}):response(current));
 await controller.load();current=row('Changed elsewhere',1);await controller.load();
 assert.equal(controller.state.draft,'Changed elsewhere');assert.equal(controller.state.value.revision,1);
 controller.edit('My draft');current=row('Third tab',2);delayed=true;const loading=controller.load();controller.edit('Newer local text');resolveRead(response(current));await loading;
 assert.equal(controller.state.phase,'conflict');assert.equal(controller.state.latest.label,'Third tab');assert.equal(controller.state.draft,'Newer local text');controller.close();
});
