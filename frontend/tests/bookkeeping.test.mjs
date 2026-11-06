import test from 'node:test';
import assert from 'node:assert/strict';
import {displayMoney,displaySigned,importStatement,parseAccountActivity,parseLedgerSnapshot,parseSourceRows,postJournal,proposeJournal,readAccountActivity} from '../.test-build/bookkeeping-client.js';
import {compositionSchema} from '../.test-build/agent-chat.js';
const id=n=>`12345678-1234-4234-8234-${String(n).padStart(12,'0')}`;
const accounts=[{id:id(2),code:'1000',name:'Checking',category:'asset'},{id:id(3),code:'3000',name:'Owner equity',category:'equity'}];
const lines=[{account_id:id(2),debit:'0.3000',credit:'0.0000'},{account_id:id(3),debit:'0.0000',credit:'0.3000'}];
const journal={id:id(4),book_id:id(1),sequence:1,operation_id:id(5),payload_hash:'a'.repeat(64),state:'draft',date:'2026-11-04',description:'Synthetic contribution',source:{kind:'manual',reference:'synthetic-source'},evidence:['synthetic-source'],debit:'0.3000',credit:'0.3000',lines};
const book={id:id(1),entity_id:id(6),currency:'USD',period_start:'2026-01-01',period_end:'2026-12-31',revision:1,accounts,journals:[journal],has_more_journals:false};
const report={book_id:id(1),currency:'USD',revision:1,accounts:accounts.map(a=>({...a,draft_debit:'0.0000',draft_credit:'0.0000',posted_debit:'0.0000',posted_credit:'0.0000',balance_debit:'0.0000',balance_credit:'0.0000'})),posted_debit:'0.0000',posted_credit:'0.0000',trial_debit:'0.0000',trial_credit:'0.0000',pending_count:1};
const fixture=()=>structuredClone({actor_id:'1',book,report});
const input={operation_id:id(5),expected_revision:0,date:journal.date,description:journal.description,source:journal.source,evidence:journal.evidence,lines:[{account_id:id(2),debit:'0.30',credit:'0'},{account_id:id(3),debit:'0',credit:'0.30'}]};

test('proposal acknowledgment must match the exact requested journal content',async()=>{
 const signal=new AbortController().signal;
 const fetcher=async url=>Response.json(url==='/api/session/csrf/'?{csrf_token:'x'.repeat(64)}:{...journal,description:'A different transaction'});
 await assert.rejects(()=>proposeJournal('1',book.id,input,signal,fetcher));
});
test('ledger snapshots reject stale actors mixed book revisions and foreign account rows',()=>{
 assert.deepEqual(parseLedgerSnapshot(fixture(),'1',book.id),fixture());
 const bad=[{...fixture(),actor_id:'2'},{...fixture(),report:{...report,revision:2}},{...fixture(),book:{...book,journals:[{...journal,lines:[{...lines[0],account_id:id(999)},lines[1]]}]}}];
 for(const value of bad)assert.throws(()=>parseLedgerSnapshot(value,'1',book.id));
});
test('posting sends exact human review hash and rejects unconfirmed or foreign receipts',async()=>{
 const calls=[],signal=new AbortController().signal;
 const receipt={state:'posted',journal_id:journal.id,actor_id:'1',payload_hash:journal.payload_hash,revision:2,at:'2026-11-04T12:00:00Z'};
 const fetcher=async(url,options)=>{calls.push({url,options});return Response.json(url==='/api/session/csrf/'?{csrf_token:'x'.repeat(64)}:receipt);};
 assert.deepEqual(await postJournal('1',book,journal,signal,fetcher),receipt);
 assert.deepEqual(JSON.parse(calls[1].options.body),{expected_revision:1,payload_hash:journal.payload_hash,confirm:true});
 assert.equal(calls[1].options.headers['X-Maipa-Agent-Actor'],'1');assert.equal(calls[1].options.credentials,'same-origin');assert.equal(calls[1].options.redirect,'error');assert.equal(calls[1].options.cache,'no-store');
 for(const change of [{actor_id:'2'},{payload_hash:'b'.repeat(64)},{revision:3},{state:'draft'}])await assert.rejects(()=>postJournal('1',book,journal,signal,async url=>Response.json(url==='/api/session/csrf/'?{csrf_token:'x'.repeat(64)}:{...receipt,...change})));
});
test('decimal display preserves large and four-decimal amounts without float rounding',()=>{
 assert.equal(displayMoney('99999999999999.9999'),'99,999,999,999,999.9999');assert.equal(displayMoney('42.1700'),'42.17');assert.equal(displayMoney('0.0000'),'0.00');
 assert.throws(()=>displayMoney('NaN'));assert.throws(()=>displayMoney('1e2'));
});
test('book components accept stable references and reject invented data authority or repeated controls',()=>{
 const schema={id:'books',type:'Maipa.Books',props:{book_id:book.id}};assert.deepEqual(compositionSchema(schema),schema);
 assert.throws(()=>compositionSchema({...schema,props:{book_id:book.id,rows:[]}}));assert.throws(()=>compositionSchema({...schema,props:{book_id:'foreign'}}));
 assert.throws(()=>compositionSchema({id:'root',type:'Maipa.Stack',children:[schema,{...schema,id:'another'}]}));
});
test('account activity keeps exact signed balances and rejects foreign or inconsistent rows',async()=>{
 const row={journal_id:id(4),sequence:1,date:'2026-03-01',description:'Synthetic contribution',source:{kind:'manual',reference:'synthetic-source'},debit:'0.0000',credit:'0.3000'};
 const activity={book_id:id(1),currency:'USD',revision:2,account:accounts[0],normal_side:'debit',posted:[{...row,balance:'-0.3000'}],pending:[{...row,journal_id:id(7),sequence:2}],posted_debit:'0.0000',posted_credit:'0.3000',balance:'-0.3000'};
 assert.deepEqual(parseAccountActivity(structuredClone(activity),id(1),id(2)),activity);
 assert.equal(displaySigned('-1234567.8900'),'−1,234,567.89');
 assert.equal(displaySigned('0.3000'),'0.30');
 for(const broken of [{...activity,book_id:id(9)},{...activity,account:accounts[1]},{...activity,balance:'0.3000'},
   {...activity,posted:[{...row,balance:'-0.0000'}],balance:'-0.0000'},{...activity,posted:[{...row,balance:'0.3'}]},
   {...activity,pending:[{...row,balance:'0.3000'}]},{...activity,actor_id:'1'}])
  assert.throws(()=>parseAccountActivity(structuredClone(broken),id(1),id(2)));
 const calls=[];
 const fetcher=async(url,options)=>{calls.push({url,options});return Response.json(activity);};
 assert.deepEqual(await readAccountActivity('1',id(1),id(2),new AbortController().signal,fetcher),activity);
 assert.equal(calls[0].url,`/api/books/${id(1)}/accounts/${id(2)}/activity/`);
 assert.equal(calls[0].options.method,'GET');
 assert.equal(calls[0].options.headers['X-Maipa-Agent-Actor'],'1');
 await assert.rejects(()=>readAccountActivity('1',id(1),'../activity',new AbortController().signal,fetcher));
 assert.equal(calls.length,1);
});
test('statement import sends exact bounded content with the observed revision and parses only consistent rows',async()=>{
 const row={id:id(8),import_id:id(9),import_filename:'synthetic-bank.csv',row_number:1,date:'2026-03-02',description:'Synthetic coffee',amount:'-0.1000',reference:'',status:'pending',journal_id:null};
 const rows={book_id:id(1),currency:'USD',revision:2,rows:[row,{...row,id:id(10),row_number:2,amount:'12.5000',status:'proposed',journal_id:id(4)}]};
 assert.deepEqual(parseSourceRows(structuredClone(rows),id(1)),rows);
 for(const broken of [{...rows,book_id:id(9)},{...rows,rows:[{...row,status:'pending',journal_id:id(4)}]},{...rows,rows:[{...row,status:'posted'}]},
   {...rows,rows:[{...row,amount:'0.0000'}]},{...rows,rows:[row,row]},{...rows,rows:[{...row,row_number:0}]},{...rows,rows:[{...row,owner:'1'}]}])
  assert.throws(()=>parseSourceRows(structuredClone(broken),id(1)));
 const calls=[];
 const saved={id:id(9),book_id:id(1),filename:'synthetic-bank.csv',sha256:'b'.repeat(64),row_count:2,created:true,created_at:'2026-11-05T00:00:00+00:00'};
 const fetcher=async(url,options)=>{calls.push({url,options});return Response.json(url==='/api/session/csrf/'?{csrf_token:'x'.repeat(64)}:saved,{status:url==='/api/session/csrf/'?200:201});};
 const content='date,description,amount\n2026-03-02,Synthetic coffee,-0.1\n';
 assert.deepEqual(await importStatement('1',book,'synthetic-bank.csv',content,new AbortController().signal,fetcher),saved);
 assert.deepEqual(JSON.parse(calls[1].options.body),{expected_revision:book.revision,filename:'synthetic-bank.csv',content});
 assert.equal(calls[1].url,`/api/books/${id(1)}/imports/`);
 assert.equal(calls[1].options.headers['X-Maipa-Agent-Actor'],'1');
 const backslash=String.fromCharCode(92);
 for(const [name,body] of [['..'+backslash+'escape.csv',content],['a/b.csv',content],['empty.csv',''],['big.csv','x'.repeat(524_289)]])
  await assert.rejects(()=>importStatement('1',book,name,body,new AbortController().signal,fetcher));
 assert.equal(calls.length,2);
 await assert.rejects(()=>importStatement('1',book,'synthetic-bank.csv',content,new AbortController().signal,async url=>Response.json(url==='/api/session/csrf/'?{csrf_token:'x'.repeat(64)}:{...saved,book_id:id(9)},{status:201})));
});
