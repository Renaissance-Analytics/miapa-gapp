// Finite application transport around the public Fancy bridge. No provider,
// browser relay, executable input, credential inheritance or business submit.
import {createInterface} from 'node:readline';
import {MicroMcpServer,errorResult} from '@particle-academy/agent-integrations/mcp';
import {registerFormBridge} from '@particle-academy/agent-integrations/bridges/forms';

const reader=createInterface({input:process.stdin,crlfDelay:Infinity}),lines=reader[Symbol.asyncIterator]();
const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
async function read(){const line=await lines.next();if(line.done||Buffer.byteLength(line.value)>65536)throw Error();return JSON.parse(line.value);}
const send=value=>process.stdout.write(JSON.stringify(value)+'\n');
try{
 const input=await read();if(!object(input)||!Array.isArray(input.forms)||input.forms.length>1)throw Error();
 const host=new MicroMcpServer({info:{name:'maipa-owned-form',version:'0.1.0'}});
 const resources=input.listOnly?[{id:'definitions-only',fields:[],values:{}}]:input.forms;
 const bridges=resources.map(form=>{
  let values={...form.values};
  const commit=async patch=>{
   send({kind:'commit',form:form.id,values:patch});const answer=await read();
   if(!object(answer)||typeof answer.ok!=='boolean')throw Error();
   if(answer.ok){if(!object(answer.values))throw Error();values={...answer.values};return {ok:true};}
   return {ok:false,error:'The shared form changed or is unavailable. Read it again before editing.',code:'stale'};
  };
  return registerFormBridge(host,{adapter:{id:form.id,title:'New client',screenId:form.component_id,
   getFields:()=>form.fields,getValue:name=>values[name],getValues:()=>({...values}),
   setValue:(name,value)=>commit({[name]:value}),setValues:commit}});
 });
 const result=input.listOnly?host.listTools().filter(tool=>tool.name.startsWith('form_')):
  await host.callTool(input.name,input.arguments);
 send({kind:'result',result});for(const bridge of bridges)bridge.dispose();
}catch{send({kind:'result',result:errorResult('Form draft unavailable')});}
finally{reader.close();process.stdin.destroy();process.stdout.end();}
