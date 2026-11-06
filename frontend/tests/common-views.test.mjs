import test from 'node:test';
import assert from 'node:assert/strict';
import {readCommonViews,CommonViewsController} from '../.test-build/common-views.js';
import {emptyChat} from '../.test-build/agent-chat.js';
const account='12345678-1234-4234-8234-123456789012',lease='12345678-1234-4234-8234-123456789013';
const metadata=(role='preparer')=>({installation_id:account,role,permissions:['workspace.read',...(role==='administrator'||role==='preparer'?['agent.transport.check']:[])]});
const request=(role='preparer')=>async()=>Response.json(metadata(role));
const live=()=>({...emptyChat(),connection:{revision:4,account_id:account,provider:'claude',label:'Claude',available:true},ui:{id:lease,account_id:account,revision:7,composition:null}});
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};

test('common header choices are finite role hints from current workspace metadata',async()=>{
 const signal=new AbortController().signal;
 assert.deepEqual((await readCommonViews('1',signal,request('administrator'))).map(v=>v.key),['agent-settings','clients','users']);
 assert.deepEqual((await readCommonViews('1',signal,request())).map(v=>v.key),['agent-settings','clients']);
 for(const role of ['viewer','reviewer'])assert.deepEqual(await readCommonViews('1',signal,request(role)),[]);
 for(const body of [{...metadata(),role:'owner'}, {...metadata(),script:'unsafe'}, {...metadata(),permissions:['workspace.read']}, {...metadata(),installation_id:'foreign'}])await assert.rejects(()=>readCommonViews('1',signal,async()=>Response.json(body)));
});

test('shortcut metadata is bounded cancellable and never follows redirects or submits chat',async()=>{
 let call;const signal=new AbortController().signal;
 await readCommonViews('1',signal,async(url,options)=>{call={url,options};return Response.json(metadata());});
 assert.equal(call.url,'/api/workspace/');assert.equal(call.options.credentials,'same-origin');assert.equal(call.options.redirect,'error');assert.equal(call.options.cache,'no-store');assert.equal(call.options.headers['X-Maipa-Agent-Actor'],'1');assert.ok(call.options.signal);assert.equal(call.options.body,undefined);
 await assert.rejects(()=>readCommonViews('1',signal,async()=>new Response('',{status:403})));
});

test('offline shortcuts open existing components without a model message lease or saved pin',async()=>{
 const state=emptyChat(),before=structuredClone(state),opened=[];let writes=0;
 const c=new CommonViewsController('1',()=>state,()=>{writes++},(v,shared)=>opened.push({v,shared}),request(),async()=>{writes++;throw Error('unexpected')});
 assert.equal(await c.open('agent-settings'),true);assert.equal(opened[0].v.composition.schema.type,'Maipa.AgentSettings');assert.equal(opened[0].shared,false);assert.equal(writes,0);assert.deepEqual(state,before);c.dispose();
});

test('live shortcuts use the current shared composition and exact acknowledged revision',async()=>{
 let state=live(),sent,published,opened;const c=new CommonViewsController('1',()=>state,ui=>{published=ui;state={...state,ui}},(v,shared)=>{opened={v,shared}},request(),async(user,ui,name,args)=>{
 sent={user,ui,name,args};return {id:ui.id,revision:ui.revision+1,composition:{title:args.title,schema:args.schema}};
 });
 assert.equal(await c.open('clients'),true);assert.equal(sent.user,'1');assert.equal(sent.name,'maipa_ui_present');assert.equal(sent.ui.id,lease);assert.equal(published.account_id,account);assert.equal(published.revision,8);assert.equal(opened.shared,true);assert.equal(state.ui.composition.schema.type,'Maipa.ClientCatalog');
 assert.equal(await c.open('agent-settings'),true);assert.equal(sent.name,'maipa_ui_update');assert.equal(published.revision,9);c.dispose();
});

test('delayed shortcut reads cannot target a changed connection or a disposed user mount',async()=>{
 let state=live(),applied=0;const wait=deferred();
 const c=new CommonViewsController('1',()=>state,()=>applied++,()=>applied++,async()=>wait.promise,async()=>{applied++;});
 const pending=c.open('agent-settings');state={...state,connection:{...state.connection,revision:5}};wait.resolve(Response.json(metadata()));await assert.rejects(()=>pending);assert.equal(applied,0);c.dispose();
 const wait2=deferred();const d=new CommonViewsController('1',()=>live(),()=>applied++,()=>applied++,async()=>wait2.promise);
 const stopped=d.open('clients');d.dispose();wait2.resolve(Response.json(metadata()));await assert.rejects(()=>stopped);assert.equal(applied,0);
});

test('role denial and overlapping shortcut clicks create no unauthorized or duplicate UI request',async()=>{
 let writes=0;const c=new CommonViewsController('1',()=>live(),()=>{},()=>{},request(),async()=>{writes++;});await assert.rejects(()=>c.open('users'));assert.equal(writes,0);c.dispose();
 const wait=deferred();let reads=0;const d=new CommonViewsController('1',()=>emptyChat(),()=>{},()=>{},async()=>{reads++;return wait.promise;});const first=d.open('clients');assert.equal(await d.open('agent-settings'),false);wait.resolve(Response.json(metadata()));assert.equal(await first,true);assert.equal(reads,1);d.dispose();
});

test('foreign or superseded UI acknowledgments are refused without replacing the visible panel',async()=>{
 let state=live(),applied=0;const c=new CommonViewsController('1',()=>state,()=>applied++,()=>applied++,request(),async()=>({id:account,revision:8,composition:{title:'Clients',schema:{id:'common-clients',type:'Maipa.ClientCatalog'}}}));await assert.rejects(()=>c.open('clients'));assert.equal(applied,0);c.dispose();
 const wait=deferred();const d=new CommonViewsController('1',()=>state,()=>applied++,()=>applied++,request(),async()=>wait.promise);const pending=d.open('agent-settings');await new Promise(r=>setTimeout(r,0));state={...state,ui:{...state.ui,revision:9}};wait.resolve({id:lease,revision:8,composition:{title:'Agent settings',schema:{id:'common-agent-settings',type:'Maipa.AgentSettings'}}});await assert.rejects(()=>pending);assert.equal(applied,0);d.dispose();
});
