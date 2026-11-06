import assert from 'node:assert/strict';
import test from 'node:test';
import { MicroMcpServer } from '@particle-academy/agent-integrations/mcp';
import { registerScreensBridge } from '@particle-academy/agent-integrations/bridges/screens';
import { onActivity } from '@particle-academy/agent-integrations/presence';
import { PersonalViewsController, parseViews } from '../.test-build/personal-views.js';

const collection='12345678-1234-4234-8234-123456789012',key='12345678-1234-4234-8234-123456789013';
const schema={id:'shared-note',type:'Maipa.Note',props:{text:'Synthetic saved note'}};
const view={id:key,title:'Saved note',revision:1,schema,available:true,pinned:true,history:[]};
const empty={views_id:collection,user_id:'7',revision:0,views:[]};
const saved={...empty,revision:1,views:[view]};
const token='a'.repeat(64);
const tick=()=>new Promise(resolve=>setImmediate(resolve));

test('saved definitions reject foreign principals unsafe schemas corrupt history and extra authority',()=>{
  assert.deepEqual(parseViews(saved,'7'),saved);
  for(const bad of [{...saved,user_id:'8'},{...saved,worker:'forged'},
    {...saved,views:[view,view]}, {...saved,revision:true},
    {...saved,views:[{...view,schema:{id:'x',type:'iframe',props:{src:'https://foreign.invalid'}}}]},
    {...saved,views:[{...view,history:[{title:'Future',revision:2,schema,available:true}]}]},
    {...saved,views:[{...view,available:false}]}])assert.throws(()=>parseViews(bad,'7'));
  const masked={...saved,views:[{...view,schema:null,available:false}]};
  assert.equal(parseViews(masked,'7').views[0].schema,null);
});

test('public Screens bridge waits for acknowledged mutation and emits no false refusal activity',async()=>{
  const server=new MicroMcpServer({info:{name:'synthetic-screens',version:'1'}}),events=[];
  const unsubscribe=onActivity(event=>events.push(event));
  let release,visible=[];const held=new Promise(resolve=>{release=resolve;});
  const bridge=registerScreensBridge(server,{adapter:{listScreens:()=>visible,getActive:()=>null,setActive:()=>{},
    createScreen:async spec=>{await held;visible=[{id:spec.id,active:true}];},
    updateScreenContent:async()=>({ok:false,error:'Synthetic conflict'})}});
  try{
    let settled=false;const result=server.callTool('screens_create',{id:key,kind:'synthetic'}).then(value=>{settled=true;return value;});
    await tick();assert.equal(settled,false);assert.deepEqual(visible,[]);assert.equal(events.length,0);
    release();assert.notEqual((await result).isError,true);assert.equal(events.length,1);
    assert.equal((await server.callTool('screens_update_content',{id:key,partial:{title:'Refused'}})).isError,true);
    assert.equal(events.length,1);
  }finally{release();bridge.dispose();unsubscribe();}
});

test('pending and failed saved-view writes keep acknowledged data without reporting success',async()=>{
  const original=globalThis.fetch,events=[];let release,posted;
  const held=new Promise(resolve=>{release=resolve;});const unsubscribe=onActivity(event=>events.push(event));
  const controller=new PersonalViewsController('7',()=>{},()=>null,()=>assert.fail('Failed save must not open'));
  globalThis.fetch=async(path,options={})=>{
    if(path==='/api/session/csrf/')return Response.json({csrf_token:token});
    if(options.method==='POST'){posted=JSON.parse(options.body);await held;return new Response('synthetic private data',{status:503});}
    return Response.json(empty);
  };
  try{
    await controller.load();const operation=controller.save({title:view.title,schema});const failure=assert.rejects(operation,/could not be confirmed/);
    await tick();assert.equal(controller.state.phase,'saving');assert.deepEqual(controller.state.value,empty);assert.equal(events.length,0);
    assert.equal(posted.expected_revision,0);assert.equal(posted.action,'create');
    release();await failure;assert.deepEqual(controller.state.value,empty);assert.equal(events.length,0);
  }finally{release();controller.dispose();unsubscribe();globalThis.fetch=original;}
});

test('acknowledged save opens only its confirmed owned definition and scopes its request',async()=>{
  const original=globalThis.fetch;let current=empty,opened;
  const controller=new PersonalViewsController('7',()=>{},()=>null,view=>{opened=view;});
  globalThis.fetch=async(path,options={})=>{
    assert.equal(options.credentials,'same-origin');assert.equal(options.cache,'no-store');assert.equal(options.redirect,'error');
    if(path==='/api/session/csrf/')return Response.json({csrf_token:token});
    if(options.method==='POST'){
      assert.equal(options.headers['X-Maipa-Views'],collection);assert.equal(options.headers['X-CSRFToken'],token);
      const body=JSON.parse(options.body);assert.deepEqual(body.schema,schema);assert.equal(body.expected_revision,0);
      current={...saved,views:[{...view,id:body.id}]};
    }
    return Response.json(current);
  };
  try{await controller.load();await controller.save({title:view.title,schema});
    assert.equal(controller.state.phase,'ready');assert.equal(controller.state.activeId,current.views[0].id);assert.deepEqual(opened,current.views[0]);
  }finally{controller.dispose();globalThis.fetch=original;}
});

test('a stale saved-view edit becomes a conflict and cannot overwrite the visible snapshot',async()=>{
  const original=globalThis.fetch;let writes=0;
  const controller=new PersonalViewsController('7',()=>{},()=>null,()=>{});
  globalThis.fetch=async(path,options={})=>{
    if(path==='/api/session/csrf/')return Response.json({csrf_token:token});
    if(options.method==='POST'){writes++;return new Response('',{status:409});}return Response.json(saved);
  };
  try{await controller.load();await assert.rejects(()=>controller.tool('screens_update_content',{id:key,partial:{title:'Stale'}}),/changed/);
    assert.equal(controller.state.phase,'conflict');assert.deepEqual(controller.state.value,saved);
    await assert.rejects(()=>controller.tool('screens_destroy',{id:key}));assert.equal(writes,1);
    await controller.load();assert.equal(controller.state.phase,'ready');
  }finally{controller.dispose();globalThis.fetch=original;}
});

test('disposing a principal during a pending save cannot publish or activate a late acknowledgment',async()=>{
  const original=globalThis.fetch;let release,updates=0;const held=new Promise(resolve=>{release=resolve;});
  const controller=new PersonalViewsController('7',()=>{updates++;},()=>null,()=>assert.fail('Closed owner must not activate'));
  globalThis.fetch=async(path,options={})=>{
    if(path==='/api/session/csrf/')return Response.json({csrf_token:token});
    if(options.method==='POST'){await held;return Response.json(saved);}return Response.json(empty);
  };
  try{await controller.load();const operation=controller.save({title:view.title,schema});const failure=assert.rejects(operation);
    await tick();controller.dispose();const count=updates;release();await failure;assert.equal(updates,count);assert.equal(controller.state.activeId,null);
  }finally{release();controller.dispose();globalThis.fetch=original;}
});

test('late reads cannot replace a newer collection and malformed owner responses clear authority',async()=>{
  const original=globalThis.fetch;let release,count=0;const held=new Promise(resolve=>{release=resolve;});
  const controller=new PersonalViewsController('7',()=>{},()=>null,()=>{});
  globalThis.fetch=async()=>{count++;if(count===1){await held;return Response.json(empty);}return Response.json(count===2?saved:{...saved,user_id:'8'});};
  try{const first=controller.load();await controller.load();release();await first;assert.deepEqual(controller.state.value,saved);
    await controller.load();assert.equal(controller.state.phase,'unavailable');assert.equal(controller.state.value,null);
  }finally{release();controller.dispose();globalThis.fetch=original;}
});
