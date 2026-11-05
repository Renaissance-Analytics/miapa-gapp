import test from 'node:test';
import assert from 'node:assert/strict';
import {parseModelPreference,ModelPreferenceController} from '../.test-build/model-preference.js';
const id='12345678-1234-4234-8234-123456789012',conversation='22345678-1234-4234-8234-123456789012';
const row=(model_id=null,revision=0)=>({user_id:'1',account_id:id,provider:'claude',revision,model_id,model_name:model_id?'Model B':null,issue:null});
const chat=()=>({id:conversation,revision:7,phase:'ready',connection:{account_id:id,revision:3,provider:'claude',label:'Claude',available:true},models:{currentModelId:'model-b',availableModels:[{modelId:'model-b',name:'Model B'}]}});
const csrf=()=>Response.json({csrf_token:'a'.repeat(64)}),tick=()=>new Promise(r=>setTimeout(r,0));

test('model preference accepts only closed original metadata with paired model values',()=>{
 assert.deepEqual(parseModelPreference(row(),'1',id),row());assert.deepEqual(parseModelPreference(row('model-b',1),'1',id),row('model-b',1));
 for(const v of [{...row(),user_id:'2'},{...row(),account_id:'foreign'},{...row(),credentials:'private'},
  {...row(),revision:true},{...row(),revision:2_000_000_000},{...row(),model_name:'Without model'},
  {...row('model-b'),model_name:null},{...row('model-b'),issue:'private-error'},{...row('model-b'),model_id:' padded '}])assert.throws(()=>parseModelPreference(v,'1',id));
});

test('remember sends only observed context and revision and never a browser model selection',async()=>{
 const current=chat(),calls=[];let saved=row();
 const request=async(url,o)=>{calls.push({url,o});return url==='/api/session/csrf/'?csrf():Response.json(o.method==='POST'?(saved=row('model-b',1)):saved);};
 const c=new ModelPreferenceController('1',id,()=>current,()=>{},request);await c.load();assert.equal(await c.remember(structuredClone(current)),true);
 const post=calls.find(x=>x.o.method==='POST');assert.equal(post.url,`/api/agent/accounts/${id}/model-preference/`);
 assert.deepEqual(JSON.parse(post.o.body),{mode:'current_model',expected_revision:0,selection_revision:3,conversation_revision:7,conversation_id:conversation});
 assert.equal(post.o.headers['X-Maipa-Agent-Actor'],'1');assert.equal(post.o.headers['X-CSRFToken'],'a'.repeat(64));
 assert.equal(post.o.credentials,'same-origin');assert.equal(post.o.redirect,'error');assert.equal(post.o.cache,'no-store');assert.ok(post.o.signal);
 assert.equal(c.state.value.model_id,'model-b');assert.deepEqual(current,chat());c.close();
});

test('stale model context and a connection changed during csrf bootstrap send zero writes',async()=>{
 let current=chat(),resolveCsrf,posts=0;const c=new ModelPreferenceController('1',id,()=>current,()=>{},async(url,o)=>url==='/api/session/csrf/'?new Promise(r=>{resolveCsrf=r;}):o.method==='POST'?(posts++,Response.json(row('model-b',1))):Response.json(row()));
 await c.load();assert.equal(await c.remember({...current,revision:6}),false);assert.equal(posts,0);
 const captured=structuredClone(current),saving=c.remember(captured);await tick();current={...current,revision:8};resolveCsrf(csrf());
 assert.equal(await saving,false);assert.equal(posts,0);assert.equal(c.state.intent.label,'Model B');c.close();
});

test('one pending write retains intention and explicit readback review prevents blind retries',async()=>{
 let saved=row(),resolveSave,posts=0;const current=chat();
 const request=async(url,o)=>url==='/api/session/csrf/'?csrf():o.method==='POST'?(posts++,await new Promise(r=>{resolveSave=r;})):Response.json(saved);
 const c=new ModelPreferenceController('1',id,()=>current,()=>{},request);await c.load();const saving=c.remember(current);await tick();
 assert.equal(await c.useDefault(),false);assert.equal(posts,1);resolveSave(new Response('',{status:409}));assert.equal(await saving,false);
 assert.equal(c.state.intent.label,'Model B');assert.equal(await c.remember(current),false);saved=row(null,1);await c.load();
 assert.equal(c.state.phase,'review');assert.equal(c.state.latest.revision,1);assert.equal(c.state.intent.label,'Model B');assert.equal(await c.useDefault(),false);
 c.acceptSaved();assert.equal(c.state.phase,'ready');assert.equal(c.state.intent,null);assert.equal(c.state.value.revision,1);assert.equal(posts,1);c.close();
});

test('lost or foreign acknowledgments keep original intent until the human accepts saved readback',async()=>{
 for(const ack of [null,{...row('model-b',1),user_id:'2'},row('wrong-model',1),row('model-b',8)]){
  let posts=0,saved=row();const c=new ModelPreferenceController('1',id,chat,()=>{},async(url,o)=>{
   if(url==='/api/session/csrf/')return csrf();if(o.method==='POST'){posts++;saved=row('model-b',1);if(ack===null)throw Error('Synthetic lost response');return Response.json(ack);}return Response.json(saved);
  });await c.load();assert.equal(await c.remember(chat()),false);assert.equal(c.state.value.revision,0);assert.equal(c.state.intent.label,'Model B');
  assert.equal(await c.remember(chat()),false);await c.load();assert.equal(c.state.phase,'review');c.acceptSaved();assert.equal(c.state.value.model_id,'model-b');assert.equal(posts,1);c.close();
 }
});

test('provider default is an explicit offline intent and disposal cannot publish or send late writes',async()=>{
 const offline={...chat(),phase:'offline',models:undefined},calls=[];
 const c=new ModelPreferenceController('1',id,()=>offline,()=>{},async(url,o)=>{calls.push({url,o});return url==='/api/session/csrf/'?csrf():Response.json(o.method==='POST'?row(null,2):row('model-b',1));});
 await c.load();assert.equal(await c.useDefault(),true);assert.deepEqual(JSON.parse(calls.find(x=>x.o.method==='POST').o.body),{mode:'provider_default',expected_revision:1});assert.equal(c.state.value.model_id,null);c.close();
 let resolve,posts=0,changes=0;const late=new ModelPreferenceController('1',id,chat,()=>changes++,async(url,o)=>url==='/api/session/csrf/'?new Promise(r=>{resolve=r;}):o.method==='POST'?(posts++,Response.json(row('model-b',1))):Response.json(row()));
 await late.load();const saving=late.remember(chat());await tick();late.close();const count=changes;resolve(csrf());assert.equal(await saving,false);assert.equal(posts,0);assert.equal(changes,count);assert.equal(late.state.phase,'closed');
});

test('browser fetch receiver semantics support reading and saving the original preference',async()=>{
 let saved=row(),posts=0;
 const request=async function(url,o){
  if(this!==undefined&&this!==globalThis)throw new TypeError('Illegal invocation');
  if(url==='/api/session/csrf/')return csrf();
  if(o.method==='POST'){posts++;saved=JSON.parse(o.body).mode==='current_model'?row('model-b',1):row(null,2);}
  return Response.json(saved);
 };
 const c=new ModelPreferenceController('1',id,chat,()=>{},request);await c.load();
 assert.equal(c.state.phase,'ready');assert.equal(await c.remember(chat()),true);assert.equal(await c.useDefault(),true);
 assert.equal(posts,2);assert.equal(c.state.value.revision,2);assert.equal(c.state.value.model_id,null);c.close();
});
