import assert from 'node:assert/strict';
import test from 'node:test';
import {WorkflowPermissionController,parseWorkflowPermission} from '../.test-build/workflow-permissions.js';
const draft='12345678-1234-4234-8234-123456789012',account='22345678-1234-4234-8234-123456789012',lease='32345678-1234-4234-8234-123456789012';
const binding={accountId:account,leaseId:lease};
const value=(enabled=false,policy_version=0)=>({draft_id:draft,draft_revision:3,account_id:account,ui_id:lease,
 entity_id:'42345678-1234-4234-8234-123456789012',entity_name:'Synthetic client',can_manage:true,enabled,policy_version,
 expires_at:enabled?'2026-11-02T23:00:00Z':null});

test('human permission change uses observed policy and graph and waits for confirmed receipt',async()=>{
 const calls=[];let finish;const c=new WorkflowPermissionController('1',draft,binding,()=>{},async command=>{
  calls.push(command);return command===undefined?value():new Promise(resolve=>{finish=()=>resolve(value(true,1));});
 });
 await c.load();const pending=c.setEnabled(true,3);await new Promise(resolve=>setImmediate(resolve));
 assert.equal(c.state.phase,'pending');assert.equal(c.state.value.enabled,false);
 assert.deepEqual(calls[1],{expected_revision:3,expected_policy_version:0,enabled:true});
 finish();await pending;assert.equal(c.state.phase,'ready');assert.equal(c.state.value.enabled,true);
});

test('uncertain result requires fresh read and never repeats or invents the grant',async()=>{
 let accepted=false,writes=0;const c=new WorkflowPermissionController('1',draft,binding,()=>{},async command=>{
  if(command===undefined)return value(accepted,accepted?1:0);writes++;accepted=true;throw Error('lost receipt');
 });
 await c.load();await c.setEnabled(true,3);assert.equal(c.state.phase,'unavailable');assert.equal(c.state.value.enabled,false);
 await c.setEnabled(true,3);assert.equal(writes,1);await c.load();assert.equal(c.state.value.enabled,true);
});

test('foreign connection extra authority fields and malformed expiry are refused before display',()=>{
 for(const change of [{ui_id:account},{account_id:lease},{draft_id:lease},{agent_id:lease},{expires_at:'tomorrow'},
  {enabled:true,expires_at:null},{policy_version:-1}])assert.throws(()=>parseWorkflowPermission({...value(),...change},draft,binding));
});

test('dispose fences pending grant acknowledgment and clears original connection state',async()=>{
 let finish;const c=new WorkflowPermissionController('1',draft,binding,()=>{},async command=>
  command===undefined?value():new Promise(resolve=>{finish=()=>resolve(value(true,1));}));
 await c.load();const pending=c.setEnabled(true,3);await new Promise(resolve=>setImmediate(resolve));
 c.dispose();finish();await pending;assert.equal(c.state.phase,'closed');assert.equal(c.state.value,null);
});

test('nonadministrator and mismatched graph cannot send an authorization change',async()=>{
 let admin=false,writes=0;const c=new WorkflowPermissionController('1',draft,binding,()=>{},async command=>{
  if(command!==undefined)writes++;return {...value(),can_manage:admin};
 });
 await c.load();await c.setEnabled(true,3);assert.equal(writes,0);
 admin=true;await c.load();await c.setEnabled(true,2);assert.equal(writes,0);
});

test('human authorization waits for a held background read instead of losing the click',async()=>{
 let reads=0,finish,writes=0;const c=new WorkflowPermissionController('1',draft,binding,()=>{},async command=>{
  if(command!==undefined){writes++;return value(true,1);}
  if(++reads===1)return value();return new Promise(resolve=>{finish=()=>resolve(value());});
 });
 await c.load();const polling=c.load();await new Promise(resolve=>setImmediate(resolve));
 const human=c.setEnabled(true,3);finish();await Promise.all([polling,human]);
 assert.equal(writes,1);assert.equal(c.state.value.enabled,true);
});
