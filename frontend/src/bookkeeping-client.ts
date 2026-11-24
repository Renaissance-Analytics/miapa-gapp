import {csrfToken} from './api.js';

export interface LedgerAccount {id:string;code:string;name:string;category:string;cash?:boolean}
export interface LedgerLine {account_id:string;debit:string;credit:string}
export interface Journal {id:string;book_id:string;sequence:number;operation_id:string;payload_hash:string;state:'draft'|'posted';date:string;description:string;source:{kind:string;reference:string};evidence:string[];debit:string;credit:string;lines:LedgerLine[]}
export interface LedgerBook {id:string;entity_id:string;currency:string;period_start:string;period_end:string;closed_through:string|null;revision:number;accounts:LedgerAccount[];journals:Journal[];has_more_journals:boolean}
export interface TrialAccount extends LedgerAccount {draft_debit:string;draft_credit:string;posted_debit:string;posted_credit:string;balance_debit:string;balance_credit:string}
export interface LedgerReport {book_id:string;currency:string;revision:number;accounts:TrialAccount[];posted_debit:string;posted_credit:string;trial_debit:string;trial_credit:string;pending_count:number}
export interface BookCatalog {actor_id:string;entities:{id:string;name:string;client:string}[];books:{id:string;entity_id:string;name:string;currency:string}[]}
export interface LedgerSnapshot {actor_id:string;book:LedgerBook;report:LedgerReport}
export interface ActivityRow {journal_id:string;sequence:number;date:string;description:string;source:{kind:string;reference:string};debit:string;credit:string}
export interface AccountActivity {book_id:string;currency:string;revision:number;account:LedgerAccount;normal_side:'debit'|'credit';posted:(ActivityRow&{balance:string})[];pending:ActivityRow[];posted_debit:string;posted_credit:string;balance:string}
export interface SourceRow {id:string;import_id:string;import_filename:string;row_number:number;date:string;description:string;amount:string;reference:string;account_id:string|null;status:'pending'|'proposed'|'posted'|'duplicate';journal_id:string|null;via:'entry'|'match'|'duplicate'|null;duplicate_of:string|null}
export interface SourceRows {book_id:string;currency:string;revision:number;rows:SourceRow[]}
export interface StatementImport {id:string;book_id:string;filename:string;sha256:string;row_count:number;account_id:string|null;opening_balance:string|null;closing_balance:string|null;created:boolean;created_at:string}
/** Largest statement accepted by the server, in UTF-8 bytes. */
export const MAX_STATEMENT_BYTES=524_288;
export interface ReportRow extends LedgerAccount {amount:string}
export interface CashAccount extends LedgerAccount {opening:string;closing:string}
export interface CashFlow {accounts:CashAccount[];opening_cash:string;closing_cash:string;net_change:string;received:string;paid:string;movements:ReportRow[];bridge:{net_income:string;not_in_cash:string;other_accounts:string;net_change:string}}
export interface FinancialReport {book_id:string;currency:string;revision:number;from:string;to:string;pending_count:number;cash_flow:CashFlow;
 income_statement:{income:ReportRow[];expenses:ReportRow[];total_income:string;total_expenses:string;net_income:string};
 balance_sheet:{as_of:string;assets:ReportRow[];liabilities:ReportRow[];equity:ReportRow[];total_assets:string;total_liabilities:string;total_equity:string;earnings_to_date:string;total_liabilities_and_equity:string}}
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
function account(v:unknown):v is LedgerAccount{return object(v)&&uuid(v.id)&&string(v.code,16)&&string(v.name,128)&&['asset','liability','equity','income','expense'].includes(String(v.category))&&(v.cash===undefined||(typeof v.cash==='boolean'&&(!v.cash||v.category==='asset')));}
function journal(v:unknown):v is Journal{return object(v)&&uuid(v.id)&&uuid(v.book_id)&&integer(v.sequence)&&uuid(v.operation_id)&&hash(v.payload_hash)&&['draft','posted'].includes(String(v.state))&&day(v.date)&&string(v.description)&&object(v.source)&&string(v.source.kind,8)&&string(v.source.reference,256)&&Array.isArray(v.evidence)&&v.evidence.length>0&&v.evidence.length<=20&&v.evidence.every(x=>string(x,256))&&amount(v.debit)&&v.debit===v.credit&&Array.isArray(v.lines)&&v.lines.length>=2&&v.lines.length<=100&&v.lines.every(l=>object(l)&&uuid(l.account_id)&&amount(l.debit)&&amount(l.credit));}
export function parseLedgerBook(v:unknown):LedgerBook{
 if(!object(v)||!exact(v,['id','entity_id','currency','period_start','period_end','closed_through','revision','accounts','journals','has_more_journals'])||(v.closed_through!==null&&!day(v.closed_through))||!uuid(v.id)||!uuid(v.entity_id)||!currency(v.currency)||!day(v.period_start)||!day(v.period_end)||!integer(v.revision)||!Array.isArray(v.accounts)||v.accounts.length>1000||!v.accounts.every(account)||new Set(v.accounts.map(a=>a.id)).size!==v.accounts.length||!Array.isArray(v.journals)||v.journals.length>100||!v.journals.every(j=>journal(j)&&j.book_id===v.id&&j.lines.every(l=>(v.accounts as LedgerAccount[]).some(a=>a.id===l.account_id)))||typeof v.has_more_journals!=='boolean')throw unavailable();
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
 if(!object(v)||!exact(v,['id','import_id','import_filename','row_number','date','description','amount','reference','account_id','status','journal_id','via','duplicate_of'])||(v.duplicate_of!==null&&!uuid(v.duplicate_of))||(v.account_id!==null&&!uuid(v.account_id))||!uuid(v.id)||!uuid(v.import_id)||!string(v.import_filename,128)||!integer(v.row_number)||v.row_number<1||!day(v.date)||!string(v.description)||!signed(v.amount)||v.amount==='0.0000'||typeof v.reference!=='string'||v.reference.length>128||!['pending','proposed','posted','duplicate'].includes(String(v.status)))return false;
 if(v.status==='duplicate')return v.journal_id===null&&v.via==='duplicate'&&uuid(v.duplicate_of)&&v.duplicate_of!==v.id;
 if(v.duplicate_of!==null)return false;
 return v.status==='pending'?v.journal_id===null&&v.via===null:uuid(v.journal_id)&&(v.via==='entry'||v.via==='match');
}
export function parseSourceRows(v:unknown,bookId:string):SourceRows{
 if(!object(v)||!exact(v,['book_id','currency','revision','rows'])||v.book_id!==bookId||!currency(v.currency)||!integer(v.revision)||!Array.isArray(v.rows)||v.rows.length>5000||!v.rows.every(r=>sourceRow(r,bookId))||new Set(v.rows.map(r=>(r as SourceRow).id)).size!==v.rows.length)throw unavailable();
 return v as unknown as SourceRows;
}
const reportRows=(v:unknown,category:string):v is ReportRow[]=>Array.isArray(v)&&v.length<=1000&&v.every(r=>object(r)&&exact(r,['id','code','name','category','amount',...('cash' in r?['cash']:[])])&&account({id:r.id,code:r.code,name:r.name,category:r.category,...('cash' in r?{cash:r.cash}:{})})&&r.category===category&&signed(r.amount));
/** Cash flow is accepted only when opening plus the movements equals closing, account by account and in total. */
function cashFlowOk(v:unknown,netIncome:unknown):v is CashFlow{
 if(!object(v)||!exact(v,['accounts','opening_cash','closing_cash','net_change','received','paid','movements','bridge'])||![v.opening_cash,v.closing_cash,v.net_change,v.received,v.paid].every(signed))return false;
 const row=(r:unknown,extra:string[]):r is Record<string,unknown>=>object(r)&&exact(r,['id','code','name','category','cash',...extra])&&account({id:r.id,code:r.code,name:r.name,category:r.category,cash:r.cash});
 if(!Array.isArray(v.accounts)||v.accounts.length>1000||!v.accounts.every(a=>row(a,['opening','closing'])&&a.category==='asset'&&a.cash===true&&signed(a.opening)&&signed(a.closing)))return false;
 if(!Array.isArray(v.movements)||v.movements.length>1000||!v.movements.every(m=>row(m,['amount'])&&m.cash===false&&signed(m.amount)&&m.amount!=='0.0000'))return false;
 const sum=(values:unknown[])=>values.reduce<bigint>((n,x)=>n+units(x as string),0n),moved=v.movements.map(m=>(m as ReportRow).amount);
 const net=units(v.net_change as string),flag=new Set([...v.accounts,...v.movements].map(r=>(r as LedgerAccount).id)).size===v.accounts.length+v.movements.length;
 const g=v.bridge;
 if(!object(g)||!exact(g,['net_income','not_in_cash','other_accounts','net_change'])||![g.net_income,g.not_in_cash,g.other_accounts,g.net_change].every(signed)||g.net_income!==netIncome||g.net_change!==v.net_change
  ||units(g.net_income as string)+units(g.not_in_cash as string)+units(g.other_accounts as string)!==net)return false;
 return flag&&sum(moved)===net&&units(v.opening_cash as string)+net===units(v.closing_cash as string)&&units(v.received as string)+units(v.paid as string)===net
  &&sum(moved.filter(x=>!x.startsWith('-')))===units(v.received as string)&&sum(v.accounts.map(a=>(a as CashAccount).closing))===units(v.closing_cash as string)
  &&sum(v.accounts.map(a=>(a as CashAccount).opening))===units(v.opening_cash as string);
}
/** A report is accepted only for this book and range, and only when the balance sheet balances and the cash flow reconciles. */
export function parseFinancialReport(v:unknown,bookId:string,from:string,to:string):FinancialReport{
 if(!object(v)||!exact(v,['book_id','currency','revision','from','to','income_statement','balance_sheet','cash_flow','pending_count'])||!object(v.income_statement)||!cashFlowOk(v.cash_flow,v.income_statement.net_income)||v.book_id!==bookId||v.from!==from||v.to!==to||!currency(v.currency)||!integer(v.revision)||!integer(v.pending_count))throw unavailable();
 const i=v.income_statement,b=v.balance_sheet;
 if(!object(i)||!exact(i,['income','expenses','total_income','total_expenses','net_income'])||!reportRows(i.income,'income')||!reportRows(i.expenses,'expense')||![i.total_income,i.total_expenses,i.net_income].every(signed))throw unavailable();
 if(!object(b)||!exact(b,['as_of','assets','liabilities','equity','total_assets','total_liabilities','total_equity','earnings_to_date','total_liabilities_and_equity'])||b.as_of!==to||!reportRows(b.assets,'asset')||!reportRows(b.liabilities,'liability')||!reportRows(b.equity,'equity')||![b.total_assets,b.total_liabilities,b.total_equity,b.earnings_to_date,b.total_liabilities_and_equity].every(signed)||b.total_assets!==b.total_liabilities_and_equity)throw unavailable();
 return v as unknown as FinancialReport;
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
export interface StatementSummary {id:string;filename:string;account_id:string|null;account:string|null;opening_balance:string|null;closing_balance:string|null;
 first_date:string;last_date:string;row_count:number;can_reconcile:boolean;reconciliation:RecordedReconciliation|null}
export interface RecordedReconciliation {recorded_at:string;as_of:string;unchanged:boolean}
export interface StatementList {book_id:string;revision:number;statements:StatementSummary[]}
export interface Reconciliation {book_id:string;currency:string;revision:number;
 statement:{id:string;filename:string;account:{id:string;code:string;name:string};opening_balance:string;closing_balance:string;first_date:string;as_of:string};
 book_balance:string;unrecorded_lines:{row_id:string;date:string;description:string;amount:string;status:'pending'|'proposed'|'posted_later'}[];unrecorded_total:string;
 outstanding_entries:{journal_id:string;sequence:number;date:string;description:string;amount:string}[];outstanding_total:string;
 adjusted_books:string;adjusted_bank:string;difference:string;reconciled:boolean;recorded:RecordedReconciliation|null}
/** Exact four-decimal amounts as integer units, for checking that reported totals add up. */
const units=(v:string)=>BigInt(v.replace('.',''));
const recordedOk=(v:unknown):v is RecordedReconciliation|null=>v===null||(object(v)&&exact(v,['recorded_at','as_of','unchanged'])&&typeof v.recorded_at==='string'&&v.recorded_at.length>=10&&v.recorded_at.length<=40&&day(v.as_of)&&typeof v.unchanged==='boolean');
/** A statement list is accepted only for this book, with each statement's readiness consistent with what it holds. */
export function parseStatements(v:unknown,bookId:string):StatementList{
 if(!object(v)||!exact(v,['book_id','revision','statements'])||v.book_id!==bookId||!integer(v.revision)||!Array.isArray(v.statements)||v.statements.length>500||!v.statements.every(s=>
  object(s)&&exact(s,['id','filename','account_id','account','opening_balance','closing_balance','first_date','last_date','row_count','can_reconcile','reconciliation'])
  &&uuid(s.id)&&string(s.filename,128)&&(s.account_id===null?s.account===null:uuid(s.account_id)&&string(s.account,160))
  &&(s.opening_balance===null?s.closing_balance===null:signed(s.opening_balance)&&signed(s.closing_balance))
  &&day(s.first_date)&&day(s.last_date)&&s.first_date<=s.last_date&&integer(s.row_count)&&s.row_count>=1
  &&s.can_reconcile===(s.account_id!==null&&s.closing_balance!==null)&&recordedOk(s.reconciliation)&&(s.reconciliation===null||s.can_reconcile===true)))throw unavailable();
 return v as unknown as StatementList;
}
/** A reconciliation is accepted only for this statement and only when its own figures add up. */
export function parseReconciliation(v:unknown,bookId:string,importId:string):Reconciliation{
 if(!object(v)||!exact(v,['book_id','currency','revision','statement','book_balance','unrecorded_lines','unrecorded_total','outstanding_entries','outstanding_total','adjusted_books','adjusted_bank','difference','reconciled','recorded'])
  ||v.book_id!==bookId||!currency(v.currency)||!integer(v.revision)||typeof v.reconciled!=='boolean'||!recordedOk(v.recorded))throw unavailable();
 const t=v.statement;
 if(!object(t)||!exact(t,['id','filename','account','opening_balance','closing_balance','first_date','as_of'])||t.id!==importId||!string(t.filename,128)||!object(t.account)||!exact(t.account,['id','code','name'])
  ||!uuid(t.account.id)||!string(t.account.code,16)||!string(t.account.name,128)||!signed(t.opening_balance)||!signed(t.closing_balance)||!day(t.first_date)||!day(t.as_of))throw unavailable();
 const lines=v.unrecorded_lines,entries=v.outstanding_entries;
 if(!Array.isArray(lines)||lines.length>500||!lines.every(l=>object(l)&&exact(l,['row_id','date','description','amount','status'])&&uuid(l.row_id)&&day(l.date)&&string(l.description)&&signed(l.amount)&&['pending','proposed','posted_later'].includes(String(l.status)))
  ||!Array.isArray(entries)||entries.length>500||!entries.every(e=>object(e)&&exact(e,['journal_id','sequence','date','description','amount'])&&uuid(e.journal_id)&&integer(e.sequence)&&day(e.date)&&string(e.description)&&signed(e.amount))
  ||![v.book_balance,v.unrecorded_total,v.outstanding_total,v.adjusted_books,v.adjusted_bank,v.difference].every(signed))throw unavailable();
 const sum=(items:{amount:string}[])=>items.reduce((total,item)=>total+units(item.amount),0n);
 const books=units(v.book_balance as string)+units(v.unrecorded_total as string),bank=units(t.closing_balance as string)+units(v.outstanding_total as string);
 if((lines.length<500&&sum(lines as {amount:string}[])!==units(v.unrecorded_total as string))||(entries.length<500&&sum(entries as {amount:string}[])!==units(v.outstanding_total as string))
  ||units(v.adjusted_books as string)!==books||units(v.adjusted_bank as string)!==bank||units(v.difference as string)!==books-bank||v.reconciled!==(books===bank))throw unavailable();
 return v as unknown as Reconciliation;
}
export async function readStatements(user:string,bookId:string,signal:AbortSignal,fetcher:typeof fetch=fetch){if(!uuid(bookId))throw unavailable();return parseStatements(await request(user,`/api/books/${bookId}/reconciliation/`,signal,undefined,fetcher),bookId);}
export async function readReconciliation(user:string,bookId:string,importId:string,signal:AbortSignal,fetcher:typeof fetch=fetch){if(!uuid(bookId)||!uuid(importId))throw unavailable();return parseReconciliation(await request(user,`/api/books/${bookId}/reconciliation/?import=${importId}`,signal,undefined,fetcher),bookId,importId);}
export async function readBooks(user:string,signal:AbortSignal,fetcher:typeof fetch=fetch):Promise<BookCatalog>{
 const v=await request(user,'/api/books/',signal,undefined,fetcher);
 if(!object(v)||!exact(v,['actor_id','entities','books'])||v.actor_id!==user||!Array.isArray(v.entities)||!v.entities.every(e=>object(e)&&uuid(e.id)&&string(e.name,128)&&string(e.client,128))||!Array.isArray(v.books)||!v.books.every(b=>object(b)&&uuid(b.id)&&uuid(b.entity_id)&&string(b.name,128)&&currency(b.currency)&&(v.entities as {id:string}[]).some(e=>e.id===b.entity_id)))throw unavailable();
 return v as unknown as BookCatalog;
}
export async function readLedger(user:string,id:string,signal:AbortSignal,fetcher:typeof fetch=fetch){if(!uuid(id))throw unavailable();return parseLedgerSnapshot(await request(user,`/api/books/${id}/`,signal,undefined,fetcher),user,id);}
export async function readAccountActivity(user:string,bookId:string,accountId:string,signal:AbortSignal,fetcher:typeof fetch=fetch){if(!uuid(bookId)||!uuid(accountId))throw unavailable();return parseAccountActivity(await request(user,`/api/books/${bookId}/accounts/${accountId}/activity/`,signal,undefined,fetcher),bookId,accountId);}
export async function readSourceRows(user:string,bookId:string,signal:AbortSignal,fetcher:typeof fetch=fetch){if(!uuid(bookId))throw unavailable();return parseSourceRows(await request(user,`/api/books/${bookId}/imports/`,signal,undefined,fetcher),bookId);}
/** accountId names the marked bank or cash account the statement is from; omit it for a file that is not a bank statement. */
export async function importStatement(user:string,b:LedgerBook,filename:string,content:string,signal:AbortSignal,fetcher:typeof fetch=fetch,accountId?:string){
 if(!string(filename,128)||/[\\/:*?"<>|]/.test(filename)||new TextEncoder().encode(content).length>MAX_STATEMENT_BYTES||!content)throw Error('Choose a CSV statement up to 512 KB with a simple file name.');
 if(accountId!==undefined&&!b.accounts.some(a=>a.id===accountId&&a.cash===true))throw Error('Choose one of your bank or cash accounts for this statement.');
 const v=await request(user,`/api/books/${b.id}/imports/`,signal,{expected_revision:b.revision,filename,content,...(accountId?{account_id:accountId}:{})},fetcher);
 if(!object(v)||!exact(v,['id','book_id','filename','sha256','row_count','account_id','opening_balance','closing_balance','created','created_at'])||v.account_id!==(accountId??null)||![v.opening_balance,v.closing_balance].every(x=>x===null||signed(x))||(v.opening_balance===null)!==(v.closing_balance===null)||!uuid(v.id)||v.book_id!==b.id||!string(v.filename,128)||!hash(v.sha256)||!integer(v.row_count)||v.row_count<1||typeof v.created!=='boolean'||typeof v.created_at!=='string')throw unavailable();
 return v as unknown as StatementImport;
}
export async function readFinancialReport(user:string,bookId:string,from:string,to:string,signal:AbortSignal,fetcher:typeof fetch=fetch){
 if(!uuid(bookId)||!day(from)||!day(to))throw unavailable();
 return parseFinancialReport(await request(user,`/api/books/${bookId}/report/?from=${from}&to=${to}`,signal,undefined,fetcher),bookId,from,to);
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

export interface CloseItemDraft {journal_id:string;sequence:number;date:string;description:string;amount:string}
export interface CloseItemLine {row_id:string;date:string;description:string;amount:string;status:'pending'|'proposed'}
export interface CloseItemStatement {import_id:string;filename:string;last_date:string;account:string|null;state:'needs_account'|'needs_balances'|'not_recorded'|'difference'|'changed'}
export interface CloseCheck {key:'drafts'|'statement_lines'|'reconciliations'|'integrity';ok:boolean;count:number;items:unknown[]}
export interface CloseReview {book_id:string;currency:string;revision:number;through:string;period_start:string;period_end:string;closed_through:string|null;
 calendar:{month:string;ends:string;state:'open'|'closed'}[];checks:CloseCheck[];can_close:boolean;blocking:string|null;ready:boolean}
const CLOSE_KEYS=['drafts','statement_lines','reconciliations','integrity'];
const BLOCKING=['ledger_period_has_drafts','ledger_period_already_closed'];
const closeItem=(key:string,i:unknown):boolean=>{
 if(!object(i))return false;
 if(key==='drafts')return exact(i,['journal_id','sequence','date','description','amount'])&&uuid(i.journal_id)&&integer(i.sequence)&&day(i.date)&&typeof i.description==='string'&&i.description.length<=500&&amount(i.amount);
 if(key==='statement_lines')return exact(i,['row_id','date','description','amount','status'])&&uuid(i.row_id)&&day(i.date)&&typeof i.description==='string'&&i.description.length<=500&&signed(i.amount)&&(i.status==='pending'||i.status==='proposed');
 if(key==='reconciliations')return exact(i,['import_id','filename','last_date','account','state'])&&uuid(i.import_id)&&typeof i.filename==='string'&&i.filename.length<=255&&day(i.last_date)&&(i.account===null||(typeof i.account==='string'&&i.account.length<=300))
  &&['needs_account','needs_balances','not_recorded','difference','changed'].includes(String(i.state));
 // An integrity finding, as verify_book reports it: the rule broken, and the entry when there is one.
 return exact(i,['code','journal_id','sequence'])&&typeof i.code==='string'&&/^[a-z_]{1,64}$/.test(i.code)
  &&((i.journal_id===null&&i.sequence===null)||(uuid(i.journal_id)&&integer(i.sequence)));
};
/** A close review is accepted only for this book and date, with the four checks in order and flags that agree with them. */
export function parseCloseReview(v:unknown,bookId:string,through:string):CloseReview{
 if(!object(v)||!exact(v,['book_id','currency','revision','through','period_start','period_end','closed_through','calendar','checks','can_close','blocking','ready'])
  ||v.book_id!==bookId||v.through!==through||!currency(v.currency)||!integer(v.revision)||!day(v.period_start)||!day(v.period_end)||(v.closed_through!==null&&!day(v.closed_through))
  ||typeof v.can_close!=='boolean'||typeof v.ready!=='boolean'||!Array.isArray(v.calendar)||v.calendar.length>120||!Array.isArray(v.checks)||v.checks.length!==4)throw unavailable();
 if(!v.calendar.every(m=>object(m)&&exact(m,['month','ends','state'])&&typeof m.month==='string'&&/^[0-9]{4}-[0-9]{2}$/.test(m.month)&&day(m.ends)&&(m.state==='open'||m.state==='closed')))throw unavailable();
 const checks=v.checks;
 if(!checks.every((c,n)=>object(c)&&exact(c,['key','ok','count','items'])&&c.key===CLOSE_KEYS[n]&&typeof c.ok==='boolean'&&integer(c.count)&&Array.isArray(c.items)&&c.items.length<=50
  &&c.ok===(c.count===0)&&c.items.length===Math.min(c.count,50)&&c.items.every(i=>closeItem(String(c.key),i))))throw unavailable();
 if(v.can_close!==(v.blocking===null)||(v.blocking!==null&&!BLOCKING.includes(String(v.blocking)))||v.ready!==(v.can_close&&checks.every(c=>(c as CloseCheck).ok)))throw unavailable();
 return v as unknown as CloseReview;
}
export async function readCloseReview(user:string,bookId:string,through:string,signal:AbortSignal,fetcher:typeof fetch=fetch){
 if(!uuid(bookId)||!day(through))throw unavailable();
 return parseCloseReview(await request(user,`/api/books/${bookId}/close-review/?through=${through}`,signal,undefined,fetcher),bookId,through);
}
