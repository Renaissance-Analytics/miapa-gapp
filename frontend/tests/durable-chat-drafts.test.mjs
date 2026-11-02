import test from 'node:test';
import assert from 'node:assert/strict';
import {DurableChatDrafts} from '../.test-build/durable-chat-drafts.js';
const account='12345678-1234-4234-8234-123456789012';
const draft=text=>({v:1,text,pills:[],attachments:[]});
const state=(revision=0,text='',pending=null,accepted=false)=>({account_id:account,revision,draft:draft(text),pending,accepted});
function server(initial=state()){
 let saved=initial;const calls=[];
 return {calls,get value(){return structuredClone(saved);},set value(v){saved=v;},request:async(key,command)=>{
  assert.equal(key,account);calls.push(command??null);if(!command)return structuredClone(saved);
  if(command.expected_revision!==saved.revision)throw Error('Conflict');
  saved={...command,account_id:account,revision:saved.revision+1,accepted:false};delete saved.expected_revision;return structuredClone(saved);
 }};
}
test('acknowledged draft and explicit empty draft restore in a fresh controller without sending',async()=>{
 const s=server(),c=new DurableChatDrafts('1',()=>{},s.request);await c.load(account);c.write(account,draft('Held Ω'));await c.save(account);
 const reload=new DurableChatDrafts('1',()=>{},s.request);await reload.load(account);assert.equal(reload.read(account).text,'Held Ω');
 reload.write(account,draft(''));await reload.save(account);const again=new DurableChatDrafts('1',()=>{},s.request);await again.load(account);assert.equal(again.read(account).text,'');
 for(const item of [c,reload,again])item.dispose();assert.equal(s.calls.filter(Boolean).length,2);
});
test('typing during a held read is retained and never written into another account',async()=>{
 let release;const calls=[];const c=new DurableChatDrafts('1',()=>{},async(key,cmd)=>{calls.push({key,cmd});if(!cmd)return new Promise(r=>{release=()=>r(state());});return {...state(1,cmd.draft.text),pending:cmd.pending};});
 const loading=c.load(account);c.write(account,draft('New typing'));release();await loading;assert.equal(c.read(account).text,'New typing');await c.save(account);
 assert.equal(calls[1].key,account);assert.equal(calls[1].cmd.draft.text,'New typing');c.dispose();
});
test('a lost save receipt preserves local input and requires explicit readback instead of overwrite',async()=>{
 let writes=0;const c=new DurableChatDrafts('1',()=>{},async(key,cmd)=>{if(!cmd)return state(2,'Saved elsewhere');writes++;throw Error('Lost receipt');});
 await c.load(account);c.write(account,draft('Keep local'));await c.save(account);assert.equal(c.status(account),'conflict');assert.equal(c.read(account).text,'Keep local');
 await c.save(account);assert.equal(writes,1);await c.resolve(account,false);assert.equal(c.read(account).text,'Saved elsewhere');c.dispose();
});
test('retry is persisted before return and reuses the same ID after reload',async()=>{
 const s=server(),c=new DurableChatDrafts('1',()=>{},s.request);await c.load(account);c.write(account,draft('Send once'));
 const id=await c.prepare(account,'Send once');assert.equal(s.value.pending.id,id);const reload=new DurableChatDrafts('1',()=>{},s.request);await reload.load(account);
 assert.equal(await reload.prepare(account,'Send once'),id);assert.equal(s.calls.filter(Boolean).length,1);c.dispose();reload.dispose();
});
test('actual accepted evidence clears only the submitted snapshot and preserves newer typing',async()=>{
 const pending={id:'22345678-1234-4234-8234-123456789012',text:'Sent',draft:draft('Sent')};
 for(const [text,expected]of [['Sent',''],['Newer typing','Newer typing']]){
  const s=server(state(3,text,pending,true)),c=new DurableChatDrafts('1',()=>{},s.request);await c.load(account);
  assert.equal(c.read(account).text,expected);await c.save(account);assert.equal(s.value.pending,null);c.dispose();
 }
});
test('foreign restoration and late disposal cannot publish another account draft',async()=>{
 const c=new DurableChatDrafts('1',()=>{},async()=>({...state(),account_id:'32345678-1234-4234-8234-123456789012'}));await c.load(account);assert.equal(c.status(account),'conflict');assert.equal(c.read(account).text,'');c.dispose();
 let release;const late=new DurableChatDrafts('1',()=>{},async()=>new Promise(r=>{release=()=>r(state(1,'Private'));}));const loading=late.load(account);late.dispose();release();await loading;assert.equal(late.status(account),'closed');assert.equal(late.read(account).text,'');
});

test('an older acknowledgment preserves edited-and-restored identical draft text',async()=>{
 const s=server(),c=new DurableChatDrafts('1',()=>{},s.request);await c.load(account);c.write(account,draft('Original'));const id=await c.prepare(account,'Original');
 c.write(account,draft('New edit'));c.write(account,draft('Original'));await c.acknowledge(account,id);
 assert.equal(c.read(account).text,'Original');assert.equal(s.value.draft.text,'Original');assert.equal(s.value.pending,null);c.dispose();
});

test('a held save serializes newer typing and acknowledgment without stale parallel writes',async()=>{
 const s=server(),calls=[];let release;const c=new DurableChatDrafts('1',()=>{},async(key,cmd)=>{
  if(cmd&&calls.length===0){calls.push(cmd);return new Promise(resolve=>{release=async()=>resolve(await s.request(key,cmd));});}
  if(cmd)calls.push(cmd);return s.request(key,cmd);
 });
 await c.load(account);c.write(account,draft('First'));const first=c.save(account);await new Promise(r=>setImmediate(r));
 c.write(account,draft('Newer'));const second=c.save(account);release();await first;await second;
 assert.equal(calls.length,2);assert.deepEqual(calls.map(x=>x.expected_revision),[0,1]);assert.equal(s.value.draft.text,'Newer');c.dispose();
});

test('typing during retry preparation cannot cancel the captured send or clear newer input',async()=>{
 const s=server();let release;const c=new DurableChatDrafts('1',()=>{},async(key,cmd)=>cmd?new Promise(resolve=>{release=async()=>resolve(await s.request(key,cmd));}):s.request(key));
 await c.load(account);c.write(account,draft('Captured send'));const preparing=c.prepare(account,'Captured send');await new Promise(r=>setImmediate(r));
 c.write(account,draft('New typing'));release();const id=await preparing;
 assert.equal(id,s.value.pending.id);assert.equal(s.value.pending.draft.text,'Captured send');assert.equal(c.read(account).text,'New typing');c.dispose();
});
test('preparation captures its original draft before awaiting a held background save',async()=>{
 const s=server();let release;let held=true;const c=new DurableChatDrafts('1',()=>{},async(key,cmd)=>{
  if(cmd&&held){held=false;return new Promise(resolve=>{release=async()=>resolve(await s.request(key,cmd));});}return s.request(key,cmd);
 });
 await c.load(account);c.write(account,draft('Original send'));const saving=c.save(account);await new Promise(r=>setImmediate(r));
 const preparing=c.prepare(account,'Original send');c.write(account,draft('New typing'));release();await saving;const id=await preparing;
 assert.equal(s.value.pending.draft.text,'Original send');await c.acknowledge(account,id);assert.equal(c.read(account).text,'New typing');c.dispose();
});

test('saved view controls cannot submit a stale reorder during an in-flight refresh',async()=>{
 const {PersonalViewsController}=await import('../.test-build/personal-views.js');
 const original=globalThis.fetch;let release,reads=0;const writes=[];
 const view=id=>({id,title:'Saved',schema:{id:'note',type:'Maipa.Note',props:{text:'Synthetic'}},revision:1,pinned:true,history:[],available:true});
 const first={views_id:'42345678-1234-4234-8234-123456789012',user_id:'1',revision:1,views:[view(account),view('22345678-1234-4234-8234-123456789012')]};
 const next={...first,revision:2};const c=new PersonalViewsController('1',()=>{},()=>null,()=>{});
 globalThis.fetch=async(url,options={})=>{
  if(String(url).includes('/csrf/'))return Response.json({csrf_token:'a'.repeat(64)});
  if(!options.method||options.method==='GET'){reads++;return reads===1?Response.json(first):new Promise(resolve=>{release=()=>resolve(Response.json(next));});}
  const command=JSON.parse(options.body);writes.push(command);return command.expected_revision===2?Response.json({...next,revision:3}):Response.json({error:{code:'revision_conflict'}},{status:409});
 };
 try{
  await c.load();const loading=c.load();await new Promise(r=>setImmediate(r));
  await assert.rejects(()=>c.reorder(first.views.map(v=>v.id).reverse()));assert.equal(writes.length,0);
  release();await loading;await c.reorder(first.views.map(v=>v.id).reverse());assert.equal(writes.length,1);assert.equal(writes[0].expected_revision,2);
 }finally{c.dispose();globalThis.fetch=original;}
});
