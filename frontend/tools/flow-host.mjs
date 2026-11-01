// Finite transport around the published authoritative Flow bridge.
import {createInterface} from 'node:readline';
import {MicroMcpServer,errorResult} from '@particle-academy/agent-integrations/mcp';
import {registerFlowBridge} from '@particle-academy/agent-integrations/bridges/flow';
const allowed=new Set(['flow_get_state','flow_list_nodes','flow_get_node','flow_list_edges','flow_get_node_schema',
 'flow_add_node','flow_update_node','flow_delete_node','flow_connect','flow_disconnect']);
const reader=createInterface({input:process.stdin,crlfDelay:Infinity}),lines=reader[Symbol.asyncIterator]();
const object=v=>!!v&&typeof v==='object'&&!Array.isArray(v);
async function read(){const row=await lines.next();if(row.done||Buffer.byteLength(row.value)>65536)throw Error();return JSON.parse(row.value);}
const send=value=>process.stdout.write(JSON.stringify(value)+'\n');
try{
 const input=await read();if(!object(input)||!object(input.graph)||!Number.isSafeInteger(input.revision))throw Error();
 let graph=structuredClone(input.graph),revision=input.revision;
 const host=new MicroMcpServer({info:{name:'maipa-owned-workflow',version:'0.1.0'}});
 const bridge=registerFlowBridge(host,{agent:{id:'maipa-author',name:'Maipa'},validateConnections:true,validateConfig:'reject',adapter:{
  getNodes:()=>structuredClone(graph.nodes),getEdges:()=>structuredClone(graph.edges),getRevision:()=>revision,
  setNodes:()=>{throw Error('Authoritative commit required');},setEdges:()=>{throw Error('Authoritative commit required');},
  commit:async op=>{
   const next=structuredClone(graph);
   switch(op.op){
    case 'add_node':next.nodes.push(op.node);break;
    case 'update_node':next.nodes=next.nodes.map(node=>node.id===op.id?op.node:node);break;
    case 'remove_node':next.nodes=next.nodes.filter(node=>node.id!==op.id);next.edges=next.edges.filter(edge=>!op.edgeIds.includes(edge.id));break;
    case 'connect':next.edges.push(op.edge);break;
    case 'disconnect':next.edges=next.edges.filter(edge=>edge.id!==op.id);break;
    default:return {ok:false,error:'Unsupported authoring operation',code:'forbidden'};
   }
   send({kind:'commit',revision:op.revision,graph:next});const answer=await read();
   if(!object(answer)||typeof answer.ok!=='boolean')throw Error();
   if(!answer.ok)return {ok:false,error:'The workflow changed or is unavailable. Read its current graph before editing.',code:'stale'};
   if(!object(answer.graph)||!Number.isSafeInteger(answer.revision))throw Error();
   graph=structuredClone(answer.graph);revision=answer.revision;return {ok:true};
  }
 }});
 const result=input.listOnly?host.listTools().filter(tool=>allowed.has(tool.name)):
  allowed.has(input.name)?await host.callTool(input.name,input.arguments):errorResult('Workflow tool unavailable');
 send({kind:'result',result});bridge.dispose();
}catch{send({kind:'result',result:errorResult('Workflow tool unavailable')});}
finally{reader.close();process.stdin.destroy();process.stdout.end();}
