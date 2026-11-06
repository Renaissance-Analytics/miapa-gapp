import {useEffect,useRef,useState} from 'react';
import {Button,Callout,DatePicker,FileUpload,Form,Heading,Input,Select,Table} from '@particle-academy/react-fancy';
import {MAX_STATEMENT_BYTES,createLedger,displayMoney,displaySigned,importStatement,postJournal,proposeJournal,readAccountActivity,readBooks,readLedger,readSourceRows,type AccountActivity,type BookCatalog,type JournalInput,type LedgerLine,type LedgerSnapshot,type SourceRows} from './bookkeeping-client.js';

/** One registered surface shared with the real agent; service rows are authoritative. */
export function Bookkeeping({userId,bookId}:{userId:string;bookId?:string}){
 const [catalog,setCatalog]=useState<BookCatalog>();const [snapshot,setSnapshot]=useState<LedgerSnapshot>();
 const [chosen,setChosen]=useState(bookId??'');const [entity,setEntity]=useState('');
 const [currency,setCurrency]=useState('USD');const year=String(new Date().getFullYear());
 const [start,setStart]=useState(year+'-01-01'),[end,setEnd]=useState(year+'-12-31');
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[status,setStatus]=useState('');
 const [review,setReview]=useState('');const pending=useRef(false),generation=useRef(0),life=useRef(new AbortController());
 const [date,setDate]=useState(new Date().toISOString().slice(0,10)),[description,setDescription]=useState(''),[reference,setReference]=useState('');
 const [lines,setLines]=useState<LedgerLine[]>([{account_id:'',debit:'',credit:'0'},{account_id:'',debit:'0',credit:''}]);
 const operation=useRef<string|undefined>(undefined);const current=useRef({userId,chosen});current.current={userId,chosen};
 const [statement,setStatement]=useState<File[]>([]);const [sources,setSources]=useState<SourceRows>();
 const [activityAccount,setActivityAccount]=useState('');const [activity,setActivity]=useState<AccountActivity>();const [activityError,setActivityError]=useState('');
 const b=snapshot?.actor_id===userId&&snapshot.book.id===chosen?snapshot.book:undefined;
 const report=b?snapshot?.report:undefined;
 async function load(id=chosen){
  const attempt=++generation.current,signal=life.current.signal;
  const c=await readBooks(userId,signal);if(signal.aborted||attempt!==generation.current)return;
  setCatalog(c);const target=id||c.books[0]?.id;
  if(!target){setSnapshot(undefined);return;}
  if(!id){setChosen(target);return;}
  const s=await readLedger(userId,target,signal);if(signal.aborted||attempt!==generation.current||current.current.userId!==userId||current.current.chosen!==target)return;
  setSnapshot(s);setError('');setReview(previous=>previous&&s.book.journals.some(j=>j.id===previous)?previous:s.book.journals[0]?.id??'');
 }
 useEffect(()=>{setChosen(bookId??'');},[bookId]);
 useEffect(()=>{
  const lifetime=new AbortController();life.current=lifetime;setSnapshot(undefined);setError('');
  pending.current=false;setBusy(false);
  void load().catch(e=>{if(!lifetime.signal.aborted)setError(String(e.message));});
  const timer=setInterval(()=>{if(!pending.current&&document.visibilityState==='visible')void load().catch(()=>{});},2000);
  return()=>{lifetime.abort();generation.current++;clearInterval(timer);};
 },[userId,chosen]);
 useEffect(()=>{setActivityAccount('');setActivity(undefined);setSources(undefined);setStatement([]);},[userId,chosen]);
 // Imported rows change with every proposal or post, so follow the book revision.
 useEffect(()=>{
  if(!b)return;
  const controller=new AbortController();
  void readSourceRows(userId,b.id,controller.signal).then(value=>{if(!controller.signal.aborted)setSources(value);}).catch(()=>{});
  return()=>controller.abort();
 },[userId,b?.id,b?.revision]);
 const shownSources=sources&&b&&sources.book_id===b.id?sources:undefined;
 const pendingRows=shownSources?.rows.filter(row=>row.status==='pending')??[];
 // Re-read activity whenever the book revision changes, so posting shows up immediately.
 useEffect(()=>{
  if(!b||!activityAccount)return;
  const controller=new AbortController();setActivityError('');
  void readAccountActivity(userId,b.id,activityAccount,controller.signal).then(value=>{if(!controller.signal.aborted)setActivity(value);})
   .catch(e=>{if(!controller.signal.aborted){setActivity(undefined);setActivityError(e instanceof Error?e.message:'Account activity unavailable.');}});
  return()=>controller.abort();
 },[userId,b?.id,b?.revision,activityAccount]);
 const shownActivity=activity&&b&&activity.book_id===b.id&&activity.account.id===activityAccount?activity:undefined;
 async function act(action:()=>Promise<void>){
  if(pending.current)return;pending.current=true;setBusy(true);setError('');setStatus('');
  const signal=life.current.signal;
  try{await action();if(!signal.aborted)await load();}
  catch(e){if(!signal.aborted)setError(e instanceof Error?e.message:'Books unavailable. Reload and review.');}
  finally{if(life.current.signal===signal){pending.current=false;if(!signal.aborted)setBusy(false);}}
 }
 const selected=b?.journals.find(j=>j.id===review),money=(v:string)=>displayMoney(v);
 const selectAccount=(i:number,key:keyof LedgerLine,value:string)=>{operation.current=undefined;setLines(old=>old.map((line,index)=>index===i?{...line,[key]:value}:line));};
 return <section className="bookkeeping" aria-label="Bookkeeping" aria-busy={busy} data-book-id={b?.id} data-book-revision={b?.revision}>
  <div className="bookkeeping-toolbar"><Heading as="h3" size="md">Books</Heading>
   <Button size="sm" variant="ghost" disabled={busy} onClick={()=>void act(async()=>{})}>Reload books</Button></div>
  {error&&<Callout color="amber" role="alert">{error}</Callout>}{status&&<p role="status">{status}</p>}
  {catalog&&<Select id="books-select" label="Business books" placeholder="Choose books" value={chosen} disabled={busy||!!bookId} onValueChange={setChosen}
   list={catalog.books.map(row=>({value:row.id,label:`${row.name} · ${row.currency}`}))}/>}
  {catalog&&!b&&!chosen&&<Form aria-label="Set up business books" className="intake-form" onSubmit={event=>{event.preventDefault();void act(async()=>{
   const result=await createLedger(userId,{entity_id:entity,currency,period_start:start,period_end:end},life.current.signal);
   setChosen(result.id);setStatus('Books created. Ask Maipa to prepare entries, or enter a journal below.');
  });}}>
   <p>Create books for an existing business. You review and confirm entries prepared by Maipa.</p>
   <Select id="books-entity" label="Business entity" placeholder="Choose a business" value={entity} onValueChange={setEntity} required list={catalog.entities.filter(e=>!catalog.books.some(row=>row.entity_id===e.id)).map(e=>({value:e.id,label:`${e.name} · ${e.client}`}))}/>
   {!catalog.entities.length&&<Callout color="blue">Create your business using Clients in the header or ask Maipa to open client setup.</Callout>}
   <Input id="books-currency" label="Book currency" value={currency} maxLength={3} onValueChange={setCurrency} required/>
   <DatePicker id="books-period-start" label="Period start" value={start} onValueChange={setStart} required/>
   <DatePicker id="books-period-end" label="Period end" value={end} onValueChange={setEnd} required/>
   <Button type="submit" disabled={busy||!entity}>Create books</Button>
  </Form>}
  {b&&report&&<>
   <p className="bookkeeping-context">{catalog?.books.find(row=>row.id===b.id)?.name} · {b.currency} · {b.period_start} to {b.period_end}</p>
   <Callout color="blue">Ask Maipa to record a transaction, categorize it or split an entry. Prepared entries appear here for your review. Posting requires your confirmation.</Callout>
   <Form aria-label="Prepare journal entry" className="intake-form" onSubmit={event=>{event.preventDefault();void act(async()=>{
    operation.current??=crypto.randomUUID();
    const value:JournalInput={operation_id:operation.current,expected_revision:b.revision,date,description,source:{kind:'manual',reference},evidence:[reference],lines:lines.map(l=>({...l,debit:l.debit||'0',credit:l.credit||'0'}))};
    const journal=await proposeJournal(userId,b.id,value,life.current.signal);setReview(journal.id);operation.current=undefined;setStatus('Entry saved for review. Your books have not changed yet.');
   });}}>
    <Heading as="h4" size="sm">Prepare an entry</Heading>
    <DatePicker id="journal-date" label="Transaction date" value={date} onValueChange={v=>{operation.current=undefined;setDate(v);}} required/>
    <Input id="journal-description" label="Description" value={description} maxLength={500} onValueChange={v=>{operation.current=undefined;setDescription(v);}} required/>
    <Input id="journal-reference" label="Source or receipt reference" value={reference} maxLength={256} onValueChange={v=>{operation.current=undefined;setReference(v);}} required/>
    {lines.map((line,i)=><div className="bookkeeping-line" key={i} data-journal-line={i}>
     <Select id={`journal-account-${i}`} label={`Account ${i+1}`} placeholder="Choose account" value={line.account_id} onValueChange={v=>selectAccount(i,'account_id',v)} required list={b.accounts.map(a=>({value:a.id,label:`${a.code} · ${a.name}`}))}/>
     <Input id={`journal-debit-${i}`} label={`Debit ${i+1}`} inputMode="decimal" value={line.debit} onValueChange={v=>selectAccount(i,'debit',v)}/>
     <Input id={`journal-credit-${i}`} label={`Credit ${i+1}`} inputMode="decimal" value={line.credit} onValueChange={v=>selectAccount(i,'credit',v)}/>
     {lines.length>2&&<Button size="sm" variant="ghost" aria-label={`Remove line ${i+1}`} onClick={()=>{operation.current=undefined;setLines(old=>old.filter((_,index)=>index!==i));}}>Remove</Button>}
    </div>)}
    <div><Button size="sm" variant="ghost" disabled={busy||lines.length>=100} onClick={()=>{operation.current=undefined;setLines(old=>[...old,{account_id:'',debit:'',credit:'0'}]);}}>Add split line</Button>{' '}
     <Button type="submit" disabled={busy}>Save for review</Button></div>
   </Form>
   <section aria-label="Import bank statement" className="bookkeeping-review" data-import-rows={shownSources?.rows.length??0}>
    <Heading as="h4" size="sm">Import a bank statement</Heading>
    <p>Upload a CSV with the columns date, description and amount (money in positive, money out negative), plus an optional reference. Every row must be valid or nothing is imported, and importing the same file again changes nothing. Imported rows are not entries yet: ask Maipa to categorize them, then review and post each entry yourself.</p>
    <FileUpload value={statement} onChange={files=>setStatement(files.slice(-1))} accept=".csv,text/csv" maxFiles={1} maxSize={MAX_STATEMENT_BYTES} disabled={busy}>
     <FileUpload.Dropzone browseLabel="Choose CSV statement">Drop a CSV statement here</FileUpload.Dropzone>
     <FileUpload.List/>
    </FileUpload>
    <div><Button disabled={busy||!statement.length} onClick={()=>void act(async()=>{
     const file=statement[0];if(!file)return;
     const saved=await importStatement(userId,b,file.name,await file.text(),life.current.signal);
     setStatement([]);setStatus(saved.created?`Imported ${saved.row_count} rows from ${saved.filename}. Ask Maipa to categorize them.`:`${saved.filename} was already imported. Nothing changed.`);
    })}>Import statement</Button></div>
    {shownSources&&!!shownSources.rows.length&&<>
     <p data-import-pending={pendingRows.length}>{pendingRows.length} of {shownSources.rows.length} imported rows still need an entry. Try asking Maipa: “Categorize my imported transactions.”</p>
     <Table aria-label="Imported statement rows"><Table.Head><Table.Row>{['Date','Description','Amount','Reference','Status'].map(t=><Table.Cell key={t} header scope="col">{t}</Table.Cell>)}</Table.Row></Table.Head>
      <Table.Body>{shownSources.rows.map(row=><Table.Row key={row.id} data-source-row={row.id} data-source-status={row.status}><Table.Cell>{row.date}</Table.Cell><Table.Cell>{row.description}</Table.Cell><Table.Cell>{displaySigned(row.amount)}</Table.Cell><Table.Cell>{row.reference||'—'}</Table.Cell><Table.Cell>{row.status==='pending'?'Needs an entry':row.status==='proposed'?'Entry awaiting your review':'Posted'}</Table.Cell></Table.Row>)}</Table.Body>
     </Table></>}
   </section>
   <Heading as="h4" size="sm">Journal · {report.pending_count} pending</Heading>
   <Table aria-label="Journal entries"><Table.Head><Table.Row>{['Date','Description','Amount','State','Review'].map(t=><Table.Cell key={t} header scope="col">{t}</Table.Cell>)}</Table.Row></Table.Head>
    <Table.Body>{b.journals.map(j=><Table.Row key={j.id} data-journal-id={j.id}><Table.Cell>{j.date}</Table.Cell><Table.Cell>{j.description}</Table.Cell><Table.Cell>{money(j.debit)}</Table.Cell><Table.Cell>{j.state==='draft'?'Pending review':'Posted'}</Table.Cell><Table.Cell><Button size="sm" variant="ghost" onClick={()=>setReview(j.id)}>View entry</Button></Table.Cell></Table.Row>)}</Table.Body>
    {!b.journals.length&&<Table.Tray>No entries yet. Ask Maipa to prepare your first transaction.</Table.Tray>}
   </Table>
   {b.has_more_journals&&<p>Showing the latest 100 entries. The trial balance includes all posted entries.</p>}
   {selected&&<section aria-label="Review journal entry" className="bookkeeping-review" data-review-journal={selected.id} data-review-hash={selected.payload_hash}>
    <Heading as="h4" size="sm">{selected.state==='draft'?'Review before posting':'Posted entry'} · {selected.description}</Heading>
    <p>{selected.date} · {b.currency} · Reference: {selected.source.reference}</p>
    <Table aria-label="Exact journal lines"><Table.Head><Table.Row>{['Account','Debit','Credit'].map(t=><Table.Cell key={t} header scope="col">{t}</Table.Cell>)}</Table.Row></Table.Head><Table.Body>{selected.lines.map((l,i)=><Table.Row key={i}><Table.Cell>{b.accounts.find(a=>a.id===l.account_id)?.name}</Table.Cell><Table.Cell>{money(l.debit)}</Table.Cell><Table.Cell>{money(l.credit)}</Table.Cell></Table.Row>)}</Table.Body></Table>
    <p>Debits {money(selected.debit)} · Credits {money(selected.credit)} · Evidence: {selected.evidence.join(', ')}</p>
    {selected.state==='draft'&&<Button disabled={busy} onClick={()=>void act(async()=>{await postJournal(userId,b,selected,life.current.signal);setStatus('Entry posted. The trial balance now includes it.');})}>Post reviewed entry</Button>}
   </section>}
   <Heading as="h4" size="sm">Trial balance · posted entries only</Heading>
   <Table aria-label="Trial balance"><Table.Head><Table.Row>{['Account','Category','Debit balance','Credit balance','Activity'].map(t=><Table.Cell key={t} header scope="col">{t}</Table.Cell>)}</Table.Row></Table.Head><Table.Body>{report.accounts.map(a=><Table.Row key={a.id} data-account-code={a.code}><Table.Cell>{a.code} · {a.name}</Table.Cell><Table.Cell>{a.category}</Table.Cell><Table.Cell>{money(a.balance_debit)}</Table.Cell><Table.Cell>{money(a.balance_credit)}</Table.Cell><Table.Cell><Button size="sm" variant="ghost" aria-pressed={activityAccount===a.id} aria-label={`View activity for ${a.code} ${a.name}`} onClick={()=>setActivityAccount(a.id)}>View activity</Button></Table.Cell></Table.Row>)}</Table.Body></Table>
   <p data-trial-totals>Debits {money(report.trial_debit)} · Credits {money(report.trial_credit)}</p>
   {activityError&&<Callout color="amber" role="alert">{activityError}</Callout>}
   {shownActivity&&<section aria-label="Account activity" className="bookkeeping-review" data-activity-account={shownActivity.account.code}>
    <Heading as="h4" size="sm">Account activity · {shownActivity.account.code} {shownActivity.account.name}</Heading>
    <p>Posted entries in date order with the running balance on the account’s normal {shownActivity.normal_side} side. A minus sign means the balance is on the opposite side. Pending entries are listed separately and are not in the balance.</p>
    <Table aria-label="Posted account activity"><Table.Head><Table.Row>{['Date','Description','Reference','Debit','Credit','Balance'].map(t=><Table.Cell key={t} header scope="col">{t}</Table.Cell>)}</Table.Row></Table.Head>
     <Table.Body>{shownActivity.posted.map((row,i)=><Table.Row key={`${row.journal_id}-${i}`} data-activity-journal={row.journal_id}><Table.Cell>{row.date}</Table.Cell><Table.Cell>{row.description}</Table.Cell><Table.Cell>{row.source.reference}</Table.Cell><Table.Cell>{money(row.debit)}</Table.Cell><Table.Cell>{money(row.credit)}</Table.Cell><Table.Cell>{displaySigned(row.balance)}</Table.Cell></Table.Row>)}</Table.Body>
     {!shownActivity.posted.length&&<Table.Tray>No posted entries for this account yet.</Table.Tray>}
    </Table>
    <p data-activity-balance>Balance {displaySigned(shownActivity.balance)} {shownActivity.currency} · Posted debits {money(shownActivity.posted_debit)} · Posted credits {money(shownActivity.posted_credit)}</p>
    {!!shownActivity.pending.length&&<p>{shownActivity.pending.length} pending {shownActivity.pending.length===1?'entry':'entries'} awaiting your review: {shownActivity.pending.map(row=>`${row.date} ${row.description}`).join('; ')}</p>}
    <Button size="sm" variant="ghost" onClick={()=>setActivityAccount('')}>Close account activity</Button>
   </section>}
  </>}
 </section>;
}
