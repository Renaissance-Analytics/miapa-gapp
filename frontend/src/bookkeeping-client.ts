import {csrfToken} from './api.js';

export interface LedgerAccount {id:string;code:string;name:string;category:string}
export interface LedgerLine {account_id:string;debit:string;credit:string}
export interface Journal {id:string;book_id:string;sequence:number;operation_id:string;payload_hash:string;state:'draft'|'posted';date:string;description:string;source:{kind:string;reference:string};evidence:string[];debit:string;credit:string;lines:LedgerLine[]}
export interface LedgerBook {id:string;entity_id:string;currency:string;period_start:string;period_end:string;revision:number;accounts:LedgerAccount[];journals:Journal[];has_more_journals:boolean}
export interface TrialAccount extends LedgerAccount {draft_debit:string;draft_credit:string;posted_debit:string;posted_credit:string;balance_debit:string;balance_credit:string}
export interface LedgerReport {book_id:string;currency:string;revision:number;accounts:TrialAccount[];posted_debit:string;posted_credit:string;trial_debit:string;trial_credit:string;pending_count:number}
export interface BookCatalog {actor_id:string;entities:{id:string;name:string;client:string}[];books:{id:string;entity_id:string;name:string;currency:string}[]}
export interface LedgerSnapshot {actor_id:string;book:LedgerBook;report:LedgerReport}
export interface ActivityRow {journal_id:string;sequence:number;date:string;description:string;source:{kind:string;reference:string};debit:string;credit:string}
export interface AccountActivity {book_id:string;currency:string;revision:number;account:LedgerAccount;normal_side:'debit'|'credit';posted:(ActivityRow&{balance:string})[];pending:ActivityRow[];posted_debit:string;posted_credit:string;balance:string}
export interface SourceRow {id:string;import_id:string;import_filename:string;row_number:number;date:string;description:string;amount:string;reference:string;status:'pending'|'proposed'|'posted';journal_id:string|null}
export interface SourceRows {book_id:string;currency:string;revision:number;rows:SourceRow[]}
export interface StatementImport {id:string;book_id:string;filename:string;sha256:string;row_count:number;created:boolean;created_at:string}
/** Largest statement accepted by the server, in UTF-8 bytes. */
export const MAX_STATEMENT_BYTES=524_288;
export interface JournalInput {operation_id:string;expected_revision:number;date:string;description:string;source:{kind:'manual';reference:string};evidence:string[];lines:LedgerLine[]}
const object=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v);
const exact=(v:Record<string,unknown>,keys:string[])=>Object.keys(v).sort().join('|')===[...keys].sort().join('|');
const uuid=(v:unknown):v is string=>typeof v==='string'&&/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(v);
const string=(v:unknown,max=500):v is string=>typeof v==='string'&&!!v.trim()&&v.length<=max&&!/[\u0000-\u001f\u007f]/.test(v);
const amount=(v:unknown):v is string=>typeof v==='string'&&/^(0|[1-9][0-9]*)\.[0-9]{4}$/.test(v)&&v.length<=50;
const integer=(v:unknown):v is number=>typeof v==='number'&&Number.isSafeInteger(v)&&v>=0;
const day=(v:unknown):v is string=>typeof v==='string'&&/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(v);
const currency=(v:unknown):v is string=>typeof v==='string'&&/^[A-Z]{3}$/.test(v);
const hash=(v:unknown):v is string=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const unavailable=()=>Error('We could not confirm the current books. Your input is kept. Reload and review before trying again.');
function account(v:unknown):v is LedgerAccount{return object(v)&&uuid(v.id)&&string(v.code,16)&&string(v.name,128)&&['asset','liability','equity','income','expense'].includes(String(v.category));}
function journal(v:unknown):v is Journal{return object(v)&&uuid(v.id)&&uuid(v.book_id)&&integer(v.sequence)&&uuid(v.operation_id)&&hash(v.payload_hash)&&['draft','posted'].includes(String(v.state))&&day(v.date)&&string(v.description)&&object(v.source)&&string(v.source.kind,8)&&string(v.source.reference,256)&&Array.isArray(v.evidence)&&v.evidence.length>0&&v.evidence.length<=20&&v.evidence.every(x=>string(x,256))&&amount(v.debit)&&v.debit===v.credit&&Array.isArray(v.lines)&&v.lines.length>=2&&v.lines.length<=100&&v.lines.every(l=>object(l)&&uuid(l.account_id)&&amount(l.debit)&&amount(l.credit));}
export function parseLedgerBook(v:unknown):LedgerBook{
 if(!object(v)||!exact(v,['id','entity_id','currency','period_start','period_end','revision','accounts','journals','has_more_journals'])||!uuid(v.id)||!uuid(v.entity_id)||!currency(v.currency)||!day(v.period_start)||!day(v.period_end)||!integer(v.revision)||!Array.isArray(v.accounts)||v.accounts.length>1000||!v.accounts.every(account)||new Set(v.accounts.map(a=>a.id)).size!==v.accounts.length||!Array.isArray(v.journals)||v.journals.length>100||!v.journals.every(j=>journal(j)&&j.book_id===v.id&&j.lines.every(l=>(v.accounts as LedgerAccount[]).some(a=>a.id===l.account_id)))||typeof v.has_more_journals!=='boolean')throw unavailable();
 return v as unknown as LedgerBook;
}
export function parseLedgerSnapshot(v:unknown,user:string,bookId:string):LedgerSnapshot{
 if(!object(v)||!exact(v,['actor_id','book','report'])||v.actor_id!==user)throw unavailable();
 const b=parseLedgerBook(v.book),r=v.report;
 if(b.id!==bookId||!object(r)||!exact(r,['book_id','currency','revision','accounts','posted_debit','posted_credit','trial_debit','trial_credit','pending_count'])||r.book_id!==b.id||r.currency!==b.currency||r.revision!==b.revision||!integer(r.pending_count)||!amount(r.posted_debit)||r.posted_debit!==r.posted_credit||!amount(r.trial_debit)||r.trial_debit!==r.trial_credit||!Array.isArray(r.accounts)||r.accounts.length!==b.accounts.length||!r.accounts.every(a=>account(a)&&['draft_debit','draft_credit','posted_debit','posted_credit','balance_debit','balance_credit'].every(k=>amount((a as unknown as Record<string,unknown>)[k]))&&b.accounts.some(original=>original.id===a.id&&original.code===a.code&&original.name===a.name&&original.category===a.category))||new Set(r.accounts.map(a=>(a as LedgerAccount).id)).size!==b.accounts.length)throw unavailable();
 return v as unknown as LedgerSnapshot;
}
const signed=(v:unknown):v is string=>{if(typeof v!=='string')return false;const text:string=v;return amount(text as unknown)||(text.startsWith('-')&&text!=='-0.0000'&&amount(text.slice(1)));};
function activityRow(v:unknown,withBalance:boolean):v is ActivityRow{return object(v)&&exact(v,['journal_id','sequence','date','description','source','debit','credit',...(withBalance?['balance']:[])])&&uuid(v.journal_id)&&integer(v.sequence)&&day(v.date)&&string(v.description)&&object(v.source)&&string(v.source.kind,8)&&string(v.source.reference,256)&&amount(v.debit)&&amount(v.credit)&&(!withBalance||signed(v.balance));}
export function parseAccountActivity(v:unknown,bookId:string,accountId:string):AccountActivity{
 if(!object(v)||!exact(v,['book_id','currency','revision','account','normal_side','posted','pending','posted_debit','posted_credit','balance'])||v.book_id!==bookId||!currency(v.currency)||!integer(v.revision)||!account(v.account)||v.account.id!==accountId||!['debit','credit'].includes(String(v.normal_side))||!Array.isArray(v.posted)||!v.posted.every(r=>activityRow(r,true))||!Array.isArray(v.pending)||!v.pending.every(r=>activityRow(r,false))||!amount(v.posted_debit)||!amount(v.posted_credit)||!signed(v.balance)||(v.posted.length?(v.posted[v.posted.length-1] as unknown as {balance:string}).balance!==v.balance:v.balance!=='0.0000'))throw unavailable();
 return v as unknown as AccountActivity;
}
/** Display only: preserve decimal digits; no Number/parseFloat arithmetic. */
export function displayMoney(value:string){if(!amount(value))throw unavailable();const [whole,fraction]=value.split('.');return whole.replace(/\B(?=(\d{3})+(?!\d))/g,',')+'.'+fraction.replace(/0+$/,'').padEnd(2,'0');}
function sourceRow(v:unknown,bookId:string):v is SourceRow{
 if(!object(v)||!exact(v,['id','import_id','import_filename','row_number','date','description','amount','reference','status','journal_id'])||!uuid(v.id)||!uuid(v.import_id)||!string(v.import_filename,128)||!integer(v.row_number)||v.row_number<1||!day(v.date)||!string(v.description)||!signed(v.amount)||v.amount==='0.0000'||typeof v.reference!=='string'||v.reference.length>128||!['pending','proposed','posted'].includes(String(v.status)))return false;
 return v.status==='pending'?v.journal_id===null:uuid(v.journal_id);
}
export function parseSourceRows(v:unknown,bookId:string):SourceRows{
 if(!object(v)||!exact(v,['book_id','currency','revision','rows'])||v.book_id!==bookId||!currency(v.currency)||!integer(v.revision)||!Array.isArray(v.rows)||v.rows.length>5000||!v.rows.every(r=>sourceRow(r,bookId))||new Set(v.rows.map(r=>(r as SourceRow).id)).size!==v.rows.length)throw unavailable();
 return v as unknown as SourceRows;
}
/** A signed running balance; negative means the account sits on its opposite side. */
export function displaySigned(value:string){if(!signed(value))throw unavailable();return value.startsWith('-')?'−'+displayMoney(value.slice(1)):displayMoney(value);}
async function request(user:string,path:string,signal:AbortSignal,body:unknown|undefined,fetcher:typeof fetch){
 if(!/^[0-9]+$/.test(user)||signal.aborted)throw unavailable();
 const headers:Record<string,string>={'X-Maipa-Agent-Actor':user};
 if(body!==undefined){headers['Content-Type']='application/json';headers['X-CSRFToken']=await csrfToken(fetcher);}
 if(signal.aborted)throw unavailable();
 const response=await fetcher(path,{method:body===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',redirect:'error',headers,signal:AbortSignal.any([signal,AbortSignal.timeout(15000)]),...(body===undefined?{}:{body:JSON.stringify(body)})});
 if(![200,201].includes(response.status)||signal.aborted)throw unavailable();
 const value:unknown=await response.json();if(signal.aborted)throw unavailable();return value;
}
export async function readBooks(user:string,signal:AbortSignal,fetcher:typeof fetch=fetch):Promise<BookCatalog>{
 const v=await request(user,'/api/books/',signal,undefined,fetcher);
 if(!object(v)||!exact(v,['actor_id','entities','books'])||v.actor_id!==user||!Array.isArray(v.entities)||!v.entities.every(e=>object(e)&&uuid(e.id)&&string(e.name,128)&&string(e.client,128))||!Array.isArray(v.books)||!v.books.every(b=>object(b)&&uuid(b.id)&&uuid(b.entity_id)&&string(b.name,128)&&currency(b.currency)&&(v.entities as {id:string}[]).some(e=>e.id===b.entity_id)))throw unavailable();
 return v as unknown as BookCatalog;
}
export async function readLedger(user:string,id:string,signal:AbortSignal,fetcher:typeof fetch=fetch){if(!uuid(id))throw unavailable();return parseLedgerSnapshot(await request(user,`/api/books/${id}/`,signal,undefined,fetcher),user,id);}
export async function readAccountActivity(user:string,bookId:string,accountId:string,signal:AbortSignal,fetcher:typeof fetch=fetch){if(!uuid(bookId)||!uuid(accountId))throw unavailable();return parseAccountActivity(await request(user,`/api/books/${bookId}/accounts/${accountId}/activity/`,signal,undefined,fetcher),bookId,accountId);}
export async function readSourceRows(user:string,bookId:string,signal:AbortSignal,fetcher:typeof fetch=fetch){if(!uuid(bookId))throw unavailable();return parseSourceRows(await request(user,`/api/books/${bookId}/imports/`,signal,undefined,fetcher),bookId);}
export async function importStatement(user:string,b:LedgerBook,filename:string,content:string,signal:AbortSignal,fetcher:typeof fetch=fetch){
 if(!string(filename,128)||/[\\/:*?"<>|]/.test(filename)||new TextEncoder().encode(content).length>MAX_STATEMENT_BYTES||!content)throw Error('Choose a CSV statement up to 512 KB with a simple file name.');
 const v=await request(user,`/api/books/${b.id}/imports/`,signal,{expected_revision:b.revision,filename,content},fetcher);
 if(!object(v)||!exact(v,['id','book_id','filename','sha256','row_count','created','created_at'])||!uuid(v.id)||v.book_id!==b.id||!string(v.filename,128)||!hash(v.sha256)||!integer(v.row_count)||v.row_count<1||typeof v.created!=='boolean'||typeof v.created_at!=='string')throw unavailable();
 return v as unknown as StatementImport;
}
export async function createLedger(user:string,value:{entity_id:string;currency:string;period_start:string;period_end:string},signal:AbortSignal,fetcher:typeof fetch=fetch){const b=parseLedgerBook(await request(user,'/api/books/',signal,value,fetcher));if(b.entity_id!==value.entity_id||b.currency!==value.currency||b.period_start!==value.period_start||b.period_end!==value.period_end)throw unavailable();return b;}
export async function proposeJournal(user:string,id:string,value:JournalInput,signal:AbortSignal,fetcher:typeof fetch=fetch){
 if(!uuid(id))throw unavailable();
 const v=await request(user,`/api/books/${id}/journals/`,signal,value,fetcher);
 const normalized=(raw:string)=>{if(!/^(0|[1-9][0-9]{0,13})(\.[0-9]{1,4})?$/.test(raw))throw unavailable();const [whole,fraction='']=raw.split('.');return whole+'.'+fraction.padEnd(4,'0');};
 if(!journal(v)||v.book_id!==id||v.operation_id!==value.operation_id||v.state!=='draft'||v.date!==value.date||v.description!==value.description||v.source.kind!==value.source.kind||v.source.reference!==value.source.reference||JSON.stringify(v.evidence)!==JSON.stringify(value.evidence)||v.lines.length!==value.lines.length||v.lines.some((line,i)=>line.account_id!==value.lines[i].account_id||line.debit!==normalized(value.lines[i].debit)||line.credit!==normalized(value.lines[i].credit)))throw unavailable();
 return v;
}
export async function postJournal(user:string,b:LedgerBook,j:Journal,signal:AbortSignal,fetcher:typeof fetch=fetch){
 if(j.state!=='draft'||j.book_id!==b.id)throw unavailable();
 const v=await request(user,`/api/books/${b.id}/journals/${j.id}/post/`,signal,{expected_revision:b.revision,payload_hash:j.payload_hash,confirm:true},fetcher);
 if(!object(v)||!exact(v,['state','journal_id','actor_id','payload_hash','revision','at'])||v.state!=='posted'||v.journal_id!==j.id||v.actor_id!==user||v.payload_hash!==j.payload_hash||v.revision!==b.revision+1||typeof v.at!=='string'||!Number.isFinite(Date.parse(v.at)))throw unavailable();
 return v;
}
