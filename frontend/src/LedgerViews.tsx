import { useEffect, useState, type ReactNode } from 'react';
import { Button, Callout, Table } from '@particle-academy/react-fancy';
import { displayMoney, displaySigned, readAccountActivity, readFinancialReport, readLedger, readReconciliation, readStatements, type AccountActivity, type FinancialReport, type LedgerSnapshot, type ReportRow,
  type Reconciliation, type StatementList, type StatementSummary } from './bookkeeping-client.js';

export type JournalFilter = 'all' | 'posted' | 'pending';
type Phase = 'loading' | 'ready' | 'unavailable';

/** Only the live scoped service supplies figures; the saved definition names which book or account to read. */
function useLive<T>(read: (signal: AbortSignal) => Promise<T>, key: string) {
  const [value, setValue] = useState<T>();
  const [phase, setPhase] = useState<Phase>('loading');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setValue(undefined); setPhase('loading');
    read(controller.signal).then(result => { if (!controller.signal.aborted) { setValue(result); setPhase('ready'); } })
      .catch(() => { if (!controller.signal.aborted) setPhase('unavailable'); });
    return () => controller.abort();
  }, [key, attempt]);
  return { value, phase, reload: () => setAttempt(count => count + 1) };
}

function Frame({ label, phase, reload, children }: { label: string; phase: Phase; reload: () => void; children: ReactNode }) {
  return <section className="client-table ledger-view" aria-label={label} aria-busy={phase === 'loading'}>
    <div className="client-table-tools"><small>{label} · from your books</small>
      <Button variant="ghost" size="sm" disabled={phase === 'loading'} onClick={reload}>{`Reload ${label.toLowerCase()}`}</Button></div>
    {phase === 'loading' && <p role="status">Reading your books…</p>}
    {phase === 'unavailable' && <Callout color="amber">We could not read this from your books. Reload to try again.</Callout>}
    {children}
  </section>;
}

const side = (value: string) => value === '0.0000' ? '' : displayMoney(value);
const head = (titles: string[]) => <Table.Head><Table.Row>{titles.map(title => <Table.Cell key={title} header scope="col">{title}</Table.Cell>)}</Table.Row></Table.Head>;

export function AccountsTable({ snapshot }: { snapshot: LedgerSnapshot }) {
  const report = snapshot.report;
  return <Table aria-label="Accounts and posted balances" data-account-count={report.accounts.length} data-book-revision={report.revision}>
    {head(['Account', 'Category', 'Debit balance', 'Credit balance'])}
    <Table.Body>{report.accounts.map(account => <Table.Row key={account.id} data-account-code={account.code}>
      <Table.Cell>{account.code} · {account.name}</Table.Cell><Table.Cell>{account.category}{account.cash === true && ' · bank or cash'}</Table.Cell>
      <Table.Cell>{side(account.balance_debit)}</Table.Cell><Table.Cell>{side(account.balance_credit)}</Table.Cell>
    </Table.Row>)}</Table.Body>
    <Table.Tray>Debit balances {displayMoney(report.trial_debit)} · Credit balances {displayMoney(report.trial_credit)} · {report.currency}
      {report.pending_count > 0 && ` · ${report.pending_count} pending ${report.pending_count === 1 ? 'entry is' : 'entries are'} not included`}</Table.Tray>
  </Table>;
}

export function JournalTable({ snapshot, show }: { snapshot: LedgerSnapshot; show: JournalFilter }) {
  const names = new Map(snapshot.book.accounts.map(account => [account.id, `${account.code} · ${account.name}`]));
  const entries = snapshot.book.journals.filter(entry => show === 'all' || (show === 'posted') === (entry.state === 'posted'));
  return <Table aria-label="Entries and their lines" data-journal-count={entries.length} data-journal-filter={show} data-book-revision={snapshot.book.revision}>
    {head(['Date', 'Entry', 'Account', 'Debit', 'Credit'])}
    <Table.Body>{entries.flatMap(entry => entry.lines.map((line, index) => <Table.Row key={`${entry.id}:${index}`} data-journal-id={entry.id} data-journal-state={entry.state}>
      <Table.Cell>{index === 0 ? entry.date : ''}</Table.Cell>
      <Table.Cell>{index === 0 ? `#${entry.sequence} ${entry.description} · ${entry.state === 'posted' ? 'Posted' : 'Pending review'}` : ''}</Table.Cell>
      <Table.Cell>{names.get(line.account_id) ?? 'Unknown account'}</Table.Cell>
      <Table.Cell>{side(line.debit)}</Table.Cell><Table.Cell>{side(line.credit)}</Table.Cell>
    </Table.Row>))}</Table.Body>
    <Table.Tray>{entries.length ? `${entries.length} ${entries.length === 1 ? 'entry' : 'entries'} · ${snapshot.book.currency}` : 'No entries to show yet.'}
      {snapshot.book.has_more_journals && ' Showing the most recent entries only.'}</Table.Tray>
  </Table>;
}

export function ActivityTable({ activity }: { activity: AccountActivity }) {
  return <Table aria-label="Posted entries and running balance" data-account-code={activity.account.code} data-activity-rows={activity.posted.length}
    data-activity-balance={activity.balance} data-book-revision={activity.revision}>
    {head(['Date', 'Entry', 'Debit', 'Credit', 'Balance'])}
    <Table.Body>{activity.posted.map(row => <Table.Row key={row.journal_id} data-journal-id={row.journal_id}>
      <Table.Cell>{row.date}</Table.Cell><Table.Cell>#{row.sequence} {row.description}</Table.Cell>
      <Table.Cell>{side(row.debit)}</Table.Cell><Table.Cell>{side(row.credit)}</Table.Cell><Table.Cell>{displaySigned(row.balance)}</Table.Cell>
    </Table.Row>)}</Table.Body>
    <Table.Tray>{activity.account.code} · {activity.account.name} · Balance {displaySigned(activity.balance)} {activity.currency}
      {!activity.posted.length && ' · No posted entries for this account yet.'}
      {activity.pending.length > 0 && ` · ${activity.pending.length} ${activity.pending.length === 1 ? 'entry is' : 'entries are'} pending review and not in this balance.`}</Table.Tray>
  </Table>;
}

export function LedgerAccounts({ userId, bookId }: { userId: string; bookId: string }) {
  const live = useLive(signal => readLedger(userId, bookId, signal), `${userId}:${bookId}`);
  return <Frame label="Chart of accounts" phase={live.phase} reload={live.reload}>{live.value && <AccountsTable snapshot={live.value} />}</Frame>;
}

export function LedgerJournal({ userId, bookId, show }: { userId: string; bookId: string; show: JournalFilter }) {
  const live = useLive(signal => readLedger(userId, bookId, signal), `${userId}:${bookId}`);
  return <Frame label="Journal entries" phase={live.phase} reload={live.reload}>{live.value && <JournalTable snapshot={live.value} show={show} />}</Frame>;
}

export function LedgerActivity({ userId, bookId, accountId }: { userId: string; bookId: string; accountId: string }) {
  const live = useLive(signal => readAccountActivity(userId, bookId, accountId, signal), `${userId}:${bookId}:${accountId}`);
  return <Frame label="Account activity" phase={live.phase} reload={live.reload}>{live.value && <ActivityTable activity={live.value} />}</Frame>;
}

/** What a statement still needs before it can be reconciled, or how its recorded reconciliation stands. */
function statementState(item: StatementSummary) {
  if (item.account_id === null) return 'Needs its bank account';
  if (item.closing_balance === null) return 'Needs its opening and closing balance';
  if (item.reconciliation === null) return 'Ready to reconcile';
  return `Recorded as of ${item.reconciliation.as_of}${item.reconciliation.unchanged ? '' : ' · figures have changed since'}`;
}

export function StatementsTable({ list }: { list: StatementList }) {
  return <Table aria-label="Imported statements" data-statement-count={list.statements.length} data-book-revision={list.revision}>
    {head(['Statement', 'Bank account', 'Dates', 'Lines', 'Opening', 'Closing', 'Reconciliation'])}
    <Table.Body>{list.statements.map(item => <Table.Row key={item.id} data-statement-id={item.id} data-can-reconcile={String(item.can_reconcile)}>
      <Table.Cell>{item.filename}</Table.Cell><Table.Cell>{item.account ?? '—'}</Table.Cell>
      <Table.Cell>{item.first_date === item.last_date ? item.first_date : `${item.first_date} to ${item.last_date}`}</Table.Cell><Table.Cell>{item.row_count}</Table.Cell>
      <Table.Cell>{item.opening_balance === null ? '—' : displaySigned(item.opening_balance)}</Table.Cell>
      <Table.Cell>{item.closing_balance === null ? '—' : displaySigned(item.closing_balance)}</Table.Cell>
      <Table.Cell>{statementState(item)}</Table.Cell>
    </Table.Row>)}</Table.Body>
    {!list.statements.length && <Table.Tray>No statements imported yet.</Table.Tray>}
  </Table>;
}

const STATUS = { pending: 'Needs an entry', proposed: 'Entry awaiting your review', posted_later: 'Posted after this date' } as const;

export function ReconciliationView({ report }: { report: Reconciliation }) {
  const t = report.statement, lower = report.difference.startsWith('-');
  return <div data-reconciled={String(report.reconciled)} data-difference={report.difference} data-book-revision={report.revision}>
    <p>{t.filename} · {t.account.code} · {t.account.name} · as of {t.as_of}</p>
    <Table aria-label="Bank against books">
      {head(['', report.currency])}
      <Table.Body>
        <Table.Row><Table.Cell>Bank closing balance</Table.Cell><Table.Cell>{displaySigned(t.closing_balance)}</Table.Cell></Table.Row>
        <Table.Row><Table.Cell>Entries the bank has not shown</Table.Cell><Table.Cell>{displaySigned(report.outstanding_total)}</Table.Cell></Table.Row>
        <Table.Row><Table.Cell>Bank, adjusted</Table.Cell><Table.Cell>{displaySigned(report.adjusted_bank)}</Table.Cell></Table.Row>
        <Table.Row><Table.Cell>Books balance</Table.Cell><Table.Cell>{displaySigned(report.book_balance)}</Table.Cell></Table.Row>
        <Table.Row><Table.Cell>Statement lines not yet posted</Table.Cell><Table.Cell>{displaySigned(report.unrecorded_total)}</Table.Cell></Table.Row>
        <Table.Row><Table.Cell>Books, adjusted</Table.Cell><Table.Cell>{displaySigned(report.adjusted_books)}</Table.Cell></Table.Row>
      </Table.Body>
    </Table>
    {report.reconciled ? <Callout color="green">The statement agrees with your books.</Callout>
      : <Callout color="amber">Unexplained difference of {displaySigned(report.difference)} {report.currency}: the books are {lower ? 'lower' : 'higher'} than the bank.</Callout>}
    <p>{report.recorded === null ? 'Not yet recorded as reviewed.'
      : `Recorded as reviewed on ${report.recorded.recorded_at.slice(0, 10)} as of ${report.recorded.as_of}${report.recorded.unchanged ? '.' : ', but the figures have changed since.'}`}</p>
    {report.unrecorded_lines.length > 0 && <Table aria-label="Statement lines not yet posted" data-unrecorded-count={report.unrecorded_lines.length}>
      {head(['Date', 'Statement line', 'Amount', 'State'])}
      <Table.Body>{report.unrecorded_lines.map(line => <Table.Row key={line.row_id}><Table.Cell>{line.date}</Table.Cell><Table.Cell>{line.description}</Table.Cell>
        <Table.Cell>{displaySigned(line.amount)}</Table.Cell><Table.Cell>{STATUS[line.status]}</Table.Cell></Table.Row>)}</Table.Body>
    </Table>}
    {report.outstanding_entries.length > 0 && <Table aria-label="Entries the bank has not shown" data-outstanding-count={report.outstanding_entries.length}>
      {head(['Date', 'Entry', 'Amount'])}
      <Table.Body>{report.outstanding_entries.map(entry => <Table.Row key={entry.journal_id}><Table.Cell>{entry.date}</Table.Cell>
        <Table.Cell>#{entry.sequence} {entry.description}</Table.Cell><Table.Cell>{displaySigned(entry.amount)}</Table.Cell></Table.Row>)}</Table.Body>
    </Table>}
  </div>;
}

export function LedgerStatements({ userId, bookId }: { userId: string; bookId: string }) {
  const live = useLive(signal => readStatements(userId, bookId, signal), `${userId}:${bookId}:statements`);
  return <Frame label="Bank statements" phase={live.phase} reload={live.reload}>{live.value && <StatementsTable list={live.value} />}</Frame>;
}

export function LedgerReconciliation({ userId, bookId, importId }: { userId: string; bookId: string; importId: string }) {
  // The list says whether this statement can be reconciled at all, so a missing account or balance is explained, not shown as an error.
  const live = useLive(async signal => {
    const item = (await readStatements(userId, bookId, signal)).statements.find(statement => statement.id === importId);
    if (!item) throw Error('Statement not found');
    return item.can_reconcile ? { item, report: await readReconciliation(userId, bookId, importId, signal) } : { item, report: null };
  }, `${userId}:${bookId}:${importId}:reconciliation`);
  return <Frame label="Bank reconciliation" phase={live.phase} reload={live.reload}>
    {live.value && (live.value.report ? <ReconciliationView report={live.value.report} />
      : <Callout color="blue" data-reconciliation-blocked={live.value.item.id}>{live.value.item.filename} cannot be reconciled yet. {statementState(live.value.item)}.</Callout>)}
  </Frame>;
}

const lines = (rows: ReportRow[], empty: string) => rows.length
  ? rows.map(item => <Table.Row key={item.id} data-account-code={item.code}><Table.Cell>{item.code} · {item.name}</Table.Cell><Table.Cell>{displaySigned(item.amount)}</Table.Cell></Table.Row>)
  : [<Table.Row key={empty}><Table.Cell>{empty}</Table.Cell><Table.Cell /></Table.Row>];
const total = (label: string, amount: string) => <Table.Row key={label} data-report-total={label}><Table.Cell><strong>{label}</strong></Table.Cell>
  <Table.Cell><strong>{displaySigned(amount)}</strong></Table.Cell></Table.Row>;

export function ReportView({ report }: { report: FinancialReport }) {
  const i = report.income_statement, b = report.balance_sheet;
  return <div data-report-from={report.from} data-report-to={report.to} data-net-income={i.net_income} data-total-assets={b.total_assets} data-book-revision={report.revision}>
    <p>Income statement · {report.from} to {report.to} · {report.currency}</p>
    <Table aria-label="Income statement">
      {head(['Account', 'Amount'])}
      <Table.Body>
        {lines(i.income, 'No income in this period.')}{total('Total income', i.total_income)}
        {lines(i.expenses, 'No expenses in this period.')}{total('Total expenses', i.total_expenses)}
        {total(i.net_income.startsWith('-') ? 'Net loss' : 'Net income', i.net_income)}
      </Table.Body>
    </Table>
    <p>Balance sheet · as of {b.as_of} · {report.currency}</p>
    <Table aria-label="Balance sheet">
      {head(['Account', 'Amount'])}
      <Table.Body>
        {lines(b.assets, 'No asset balances.')}{total('Total assets', b.total_assets)}
        {lines(b.liabilities, 'No liability balances.')}{total('Total liabilities', b.total_liabilities)}
        {lines(b.equity, 'No equity balances.')}{total('Total equity', b.total_equity)}{total('Earnings to date', b.earnings_to_date)}
        {total('Total liabilities and equity', b.total_liabilities_and_equity)}
      </Table.Body>
    </Table>
    {report.pending_count > 0 && <p>{report.pending_count} pending {report.pending_count === 1 ? 'entry is' : 'entries are'} not included.</p>}
  </div>;
}

export function LedgerReport({ userId, bookId, from, to }: { userId: string; bookId: string; from: string; to: string }) {
  const live = useLive(signal => readFinancialReport(userId, bookId, from, to, signal), `${userId}:${bookId}:${from}:${to}:report`);
  return <Frame label="Financial statements" phase={live.phase} reload={live.reload}>{live.value && <ReportView report={live.value} />}</Frame>;
}
