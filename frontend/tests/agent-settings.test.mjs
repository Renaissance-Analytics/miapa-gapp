import test from 'node:test';
import assert from 'node:assert/strict';
import {parseSettings,readSettings,settingsCommand,applySettingsCommand} from '../.test-build/agent-settings.js';
import {emptyChat} from '../.test-build/agent-chat.js';
const id='12345678-1234-4234-8234-123456789012';
const metadata=()=>({user_id:'1',providers:[{id:'claude',label:'Claude',available:true},{id:'independent',label:'Independent',available:false}],accounts:[{id,provider:'claude',label:'My account',profile_assigned:true}]});
const state=()=>({...emptyChat(),revision:7,phase:'ready',connection:{revision:3,account_id:id,provider:'claude',label:'Claude',available:true},providers:metadata().providers,models:{currentModelId:'a',availableModels:[{modelId:'a',name:'Model A'},{modelId:'b',name:'Model B'}]}});
test('settings are owned closed metadata and never credentials or readiness',()=>{
 assert.deepEqual(parseSettings(metadata(),'1'),metadata());
 for(const v of [{...metadata(),user_id:'2'},{...metadata(),token:'secret'},{...metadata(),accounts:[{...metadata().accounts[0],home:'/private'}]},{...metadata(),accounts:[{...metadata().accounts[0],ready:true}]}])assert.throws(()=>parseSettings(v,'1'));
});
test('settings reads are same-origin captured-actor and have a bounded cancellable lifetime',async()=>{
 let call;const value=await readSettings('1',new AbortController().signal,async(url,options)=>{call={url,options};return Response.json(metadata());});
 assert.equal(value.accounts[0].id,id);assert.equal(call.url,'/api/agent/settings/');assert.equal(call.options.headers['X-Maipa-Agent-Actor'],'1');assert.equal(call.options.credentials,'same-origin');assert.equal(call.options.cache,'no-store');assert.equal(call.options.redirect,'error');assert.ok(call.options.signal);
 await assert.rejects(()=>readSettings('1',new AbortController().signal,async()=>Response.json({...metadata(),user_id:'2'})));
 await assert.rejects(()=>readSettings('1',new AbortController().signal,async()=>new Response('',{status:403})));
});
test('model commands use only acknowledged live choices and exact observed revisions',()=>{
 const s=state();assert.deepEqual(settingsCommand(s,'model','b'),{action:'set_model',revision:7,selection_revision:3,model_id:'b'});
 for(const bad of [{...s,phase:'thinking'},{...s,phase:'offline'},{...s,models:undefined}])assert.throws(()=>settingsCommand(bad,'model','b'));
 assert.throws(()=>settingsCommand(s,'model','foreign'));assert.throws(()=>settingsCommand(s,'provider','independent'));
 assert.deepEqual(settingsCommand({...s,phase:'offline'},'provider','claude'),{action:'select_provider',selection_revision:3,provider:'claude'});
});
test('a stale settings control cannot send a command to a newer selected connection',async()=>{
 const captured=state(),calls=[];const invoke=async command=>{calls.push(command);return true;};
 assert.equal(await applySettingsCommand(captured,{...captured,connection:{...captured.connection,revision:4}},'model','b',invoke),false);assert.equal(calls.length,0);
 assert.equal(await applySettingsCommand(captured,{...captured,revision:8},'model','b',invoke),false);assert.equal(calls.length,0);
 assert.equal(await applySettingsCommand(captured,captured,'model','b',invoke),true);assert.equal(calls.length,1);assert.equal(calls[0].model_id,'b');
});
