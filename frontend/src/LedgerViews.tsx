import { useEffect, useState, type ReactNode } from 'react';
import { Badge, Button, Callout, Table } from '@particle-academy/react-fancy';
import { displayMoney, displaySigned, readAccountActivity, readCloseReview, readForecast, readReleases, readFinancialReport, readLedger, readReconciliation, readStatements, type AccountActivity, type CloseItemDraft, type CloseItemLine, type CloseItemStatement, type CashForecast, type CloseReview, type ReleaseList, type FinancialReport, type LedgerSnapshot, type ReportRow,
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

const cashLine = (label: string, amount: string, strong = false) => <Table.Row key={label} data-cash-total={label}>
  <Table.Cell>{strong ? <strong>{label}</strong> : label}</Table.Cell><Table.Cell>{strong ? <strong>{displaySigned(amount)}</strong> : displaySigned(amount)}</Table.Cell></Table.Row>;

export function ReportView({ report }: { report: FinancialReport }) {
  const i = report.income_statement, b = report.balance_sheet, c = report.cash_flow;
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
    <p>Cash flow · {report.from} to {report.to} · {report.currency}</p>
    {c.accounts.length === 0 ? <Callout color="blue" data-cash-flow="unmarked">No account is marked as a bank account or cash on hand yet, so there is no cash flow to show.</Callout>
      : <Table aria-label="Cash flow" data-cash-flow="shown" data-net-cash={c.net_change} data-closing-cash={c.closing_cash}>
        {head(['Bank and cash', 'Amount'])}
        <Table.Body>
          {cashLine('Cash at the start', c.opening_cash, true)}
          {c.movements.map(item => <Table.Row key={item.id} data-cash-movement={item.code}><Table.Cell>{item.code} · {item.name}</Table.Cell><Table.Cell>{displaySigned(item.amount)}</Table.Cell></Table.Row>)}
          {c.movements.length === 0 && <Table.Row><Table.Cell>No cash moved in this period.</Table.Cell><Table.Cell /></Table.Row>}
          {cashLine('Cash received', c.received)}{cashLine('Cash paid', c.paid)}{cashLine('Change in cash', c.net_change, true)}
          {c.accounts.map(item => <Table.Row key={item.id} data-cash-account={item.code}><Table.Cell>{item.code} · {item.name} at the end</Table.Cell><Table.Cell>{displaySigned(item.closing)}</Table.Cell></Table.Row>)}
          {cashLine('Cash at the end', c.closing_cash, true)}
        </Table.Body>
      </Table>}
    {c.accounts.length > 0 && <Table aria-label="From profit to cash" data-cash-bridge={c.bridge.net_change}>
      {head(['Why cash changed differently from profit', 'Amount'])}
      <Table.Body>
        {cashLine(c.bridge.net_income.startsWith('-') ? 'Net loss' : 'Net income', c.bridge.net_income)}
        {cashLine('Income and expenses not yet received or paid', c.bridge.not_in_cash)}
        {cashLine('Cash moved by other accounts', c.bridge.other_accounts)}
        {cashLine('Change in cash', c.bridge.net_change, true)}
      </Table.Body>
    </Table>}
    {report.pending_count > 0 && <p>{report.pending_count} pending {report.pending_count === 1 ? 'entry is' : 'entries are'} not included.</p>}
  </div>;
}

export function LedgerReport({ userId, bookId, from, to }: { userId: string; bookId: string; from: string; to: string }) {
  const live = useLive(signal => readFinancialReport(userId, bookId, from, to, signal), `${userId}:${bookId}:${from}:${to}:report`);
  return <Frame label="Financial statements" phase={live.phase} reload={live.reload}>{live.value && <ReportView report={live.value} />}</Frame>;
}

const CHECK_TITLES = { drafts: 'Draft entries', statement_lines: 'Statement lines without an entry', reconciliations: 'Statements not reconciled', integrity: 'Entries pass the integrity check' } as const;
const STATEMENT_STATES = { needs_account: 'Needs its bank account', needs_balances: 'Needs its opening and closing balance', not_recorded: 'Agrees, not yet recorded as reviewed',
  difference: 'Has an unexplained difference', changed: 'Changed since it was recorded' } as const;
const LINE_STATES = { pending: 'Needs an entry', proposed: 'Entry awaiting your review' } as const;
const BLOCKS: Record<string, string> = { ledger_period_has_drafts: 'A draft entry is dated in this period, so the books cannot be closed until it is posted or discarded.',
  ledger_period_already_closed: 'The books are already closed through this date or later.' };

export function CloseReviewView({ review }: { review: CloseReview }) {
  const [drafts, lines, statements, integrity] = review.checks;
  return <div data-close-through={review.through} data-close-ready={String(review.ready)} data-close-can={String(review.can_close)} data-book-revision={review.revision}>
    <p>Closing through {review.through} · {review.currency}{review.closed_through ? ` · Closed through ${review.closed_through}` : ''}</p>
    <ol className="close-calendar" aria-label="Months of the book period">
      {review.calendar.map(month => <li key={month.month} data-close-month={month.month} data-month-state={month.state}>{month.month} · {month.state === 'closed' ? 'Closed' : 'Open'}</li>)}
    </ol>
    {review.blocking ? <Callout color="amber">{BLOCKS[review.blocking]}</Callout>
      : review.ready ? <Callout color="green">Nothing is outstanding. Closing stays your decision.</Callout>
      : <Callout color="blue">The books can be closed, but some items are still open. Closing stays your decision.</Callout>}
    {review.checks.map(check => <section key={check.key} data-check={check.key} data-check-ok={String(check.ok)} aria-label={CHECK_TITLES[check.key]}>
      <p><strong>{CHECK_TITLES[check.key]}</strong> · {check.ok ? (check.key === 'integrity' ? 'No findings' : 'None') : `${check.count} open`}</p>
      {check === drafts && !check.ok && <Table aria-label="Draft entries in this period">{head(['Date', 'Entry', 'Amount'])}
        <Table.Body>{(check.items as CloseItemDraft[]).map(item => <Table.Row key={item.journal_id}><Table.Cell>{item.date}</Table.Cell>
          <Table.Cell>#{item.sequence} {item.description}</Table.Cell><Table.Cell>{displayMoney(item.amount)}</Table.Cell></Table.Row>)}</Table.Body></Table>}
      {check === lines && !check.ok && <Table aria-label="Statement lines without an entry">{head(['Date', 'Statement line', 'Amount', 'State'])}
        <Table.Body>{(check.items as CloseItemLine[]).map(item => <Table.Row key={item.row_id}><Table.Cell>{item.date}</Table.Cell><Table.Cell>{item.description}</Table.Cell>
          <Table.Cell>{displaySigned(item.amount)}</Table.Cell><Table.Cell>{LINE_STATES[item.status]}</Table.Cell></Table.Row>)}</Table.Body></Table>}
      {check === statements && !check.ok && <Table aria-label="Statements not reconciled">{head(['Statement', 'Bank account', 'Last date', 'State'])}
        <Table.Body>{(check.items as CloseItemStatement[]).map(item => <Table.Row key={item.import_id}><Table.Cell>{item.filename}</Table.Cell><Table.Cell>{item.account ?? '—'}</Table.Cell>
          <Table.Cell>{item.last_date}</Table.Cell><Table.Cell>{STATEMENT_STATES[item.state]}</Table.Cell></Table.Row>)}</Table.Body></Table>}
      {check === integrity && !check.ok && <p>{check.count} {check.count === 1 ? 'finding' : 'findings'}: ask Maipa to explain them before closing.</p>}
      {check.count > check.items.length && <p>Showing the first {check.items.length} of {check.count}.</p>}
    </section>)}
  </div>;
}

export function LedgerCloseReview({ userId, bookId, through }: { userId: string; bookId: string; through: string }) {
  const live = useLive(signal => readCloseReview(userId, bookId, through, signal), `${userId}:${bookId}:${through}:close-review`);
  return <Frame label="Close review" phase={live.phase} reload={live.reload}>{live.value && <CloseReviewView review={live.value} />}</Frame>;
}

export function ReleasesView({ list }: { list: ReleaseList }) {
  if (!list.releases.length) return <div data-release-count="0" data-book-revision={list.revision}>
    <Callout color="blue">No statements have been released yet. {list.closed_through ? `The books are closed through ${list.closed_through}; ask Maipa to release the statements for a closed period.`
      : 'Statements can be released once a period is closed.'}</Callout></div>;
  return <div data-release-count={list.releases.length} data-book-revision={list.revision}>
    <p>{list.currency}{list.closed_through ? ` · Closed through ${list.closed_through}` : ' · No period is closed now'}</p>
    {list.releases.some(item => item.latest && !item.unchanged) && <Callout color="amber">A release no longer matches the books. Its figures are kept as released; ask Maipa what changed, or to release the period again.</Callout>}
    {/* One block per release: a wide table breaks figures mid-number in the narrow panel. */}
    <div className="release-list" role="list" aria-label="Released statements">{list.releases.map(item => {
      const state = !item.latest ? 'replaced' : item.unchanged ? 'current' : 'changed';
      return <section key={item.id} role="listitem" className="release-item" data-release-id={item.id} data-release-number={item.number} data-release-state={state}>
        <p><strong>No. {item.number} · {item.title}</strong></p>
        <p>{item.from} to {item.to}</p>
        <p>Released {item.released_at.slice(0, 10)}</p>
        <p><Badge color={state === 'current' ? 'green' : state === 'changed' ? 'amber' : 'zinc'} variant="soft">
          {state === 'replaced' ? 'Replaced by a newer release' : state === 'current' ? 'Matches the books' : 'Out of date: the books have changed'}</Badge></p>
        <Table aria-label={`Figures of release ${item.number}`}>{head(['Figure', 'Amount'])}
          <Table.Body>{([['Income', item.totals.total_income], ['Expenses', item.totals.total_expenses], ['Net income', item.totals.net_income],
            ['Assets', item.totals.total_assets], ['Liabilities', item.totals.total_liabilities], ['Cash at the end', item.totals.closing_cash]] as const).map(([label, value]) =>
            <Table.Row key={label}><Table.Cell>{label}</Table.Cell><Table.Cell className="figure">{displaySigned(value)}</Table.Cell></Table.Row>)}</Table.Body></Table>
      </section>; })}</div>
  </div>;
}

export function LedgerReleases({ userId, bookId }: { userId: string; bookId: string }) {
  const live = useLive(signal => readReleases(userId, bookId, signal), `${userId}:${bookId}:releases`);
  return <Frame label="Released statements" phase={live.phase} reload={live.reload}>{live.value && <ReleasesView list={live.value} />}</Frame>;
}

const SOURCES = { posted: 'Posted', draft: 'Awaiting your approval', recurring: 'Recurring, not drafted yet' } as const;
const below = (value: string) => value.startsWith('-');

export function ForecastView({ forecast }: { forecast: CashForecast }) {
  const plan = forecast.plan;
  const planLabel = plan?.scenario ? `With plan and "${plan.scenario}"` : 'With your plan';
  const weekOf = (number: number) => forecast.weeks[number - 1].start;
  return <div data-forecast-from={forecast.from} data-forecast-plan={String(plan !== null)} data-forecast-scenario={plan?.scenario ?? ''} data-book-revision={forecast.revision}>
    <p>13 weeks from {forecast.from} to {forecast.through} · {forecast.currency}</p>
    <p>Usable cash at the start: <strong className="figure">{displaySigned(forecast.opening_usable)}</strong></p>
    {forecast.restricted_cash !== '0.0000' && <p>Set aside, not counted as usable: <span className="figure">{displaySigned(forecast.restricted_cash)}</span></p>}
    {below(forecast.lowest.closing) ? <Callout color="red">On what is in your books, usable cash goes below zero in the week of {weekOf(forecast.lowest.week)}.</Callout>
      : plan && below(plan.lowest.closing) ? <Callout color="amber">{planLabel}, usable cash goes below zero in the week of {weekOf(plan.lowest.week)}.</Callout>
      : <Callout color="green">Usable cash stays above zero in all 13 weeks{plan ? ', also with your plan' : ''}.</Callout>}
    <Table aria-label="Usable cash by week">{head(plan ? ['Week of', 'From the books', planLabel] : ['Week of', 'From the books'])}
      <Table.Body>{forecast.weeks.map((week, index) => <Table.Row key={week.week} data-week={week.week}
        data-lowest={String(week.week === forecast.lowest.week)} data-plan-lowest={String(plan !== null && week.week === plan.lowest.week)}>
        <Table.Cell className="nowrap">{week.start}</Table.Cell><Table.Cell className="figure">{displaySigned(week.closing)}</Table.Cell>
        {plan && <Table.Cell className="figure">{displaySigned(plan.weeks[index].closing)}</Table.Cell>}</Table.Row>)}</Table.Body></Table>
    <p>Lowest from the books: <span className="figure">{displaySigned(forecast.lowest.closing)}</span> in the week of {weekOf(forecast.lowest.week)}.
      {plan && <> {planLabel}: <span className="figure">{displaySigned(plan.lowest.closing)}</span> in the week of {weekOf(plan.lowest.week)}.</>}</p>
    <section aria-label="What moves each week"><p><strong>What moves</strong></p>
      {forecast.weeks.every((week, index) => !week.items.length && !(plan && plan.weeks[index].items.length)) ? <p>Nothing in your books or plan moves usable cash in these weeks.</p>
        : forecast.weeks.map((week, index) => (week.items.length || (plan && plan.weeks[index].items.length)) ? <div key={week.week} className="forecast-week" data-week-items={week.week}>
          <p>Week of {week.start}</p>
          <ul>{week.items.map((item, n) => <li key={`b${n}`} data-item-source={item.source}>{item.date} · {item.description} · <span className="figure">{displaySigned(item.amount)}</span> · {SOURCES[item.source]}</li>)}
            {plan && plan.weeks[index].items.map((item, n) => <li key={`p${n}`} data-item-source={item.scenario ? 'scenario' : 'planned'}>{item.date} · {item.description} · <span className="figure">{displaySigned(item.amount)}</span> · {item.scenario ? `What-if "${item.scenario}"` : 'Planned'}</li>)}</ul>
        </div> : null)}</section>
    <section aria-label="Your cash plan"><p><strong>Your cash plan</strong></p>
      {forecast.plan_items.length ? <ul>{forecast.plan_items.map(item => <li key={item.id} data-plan-item={item.id}>{item.description} · <span className="figure">{displaySigned(item.amount)}</span> · {item.date}
        {item.every === 'week' ? ', then every week' : item.every === 'month' ? ', then every month' : ''}{item.until ? ` until ${item.until}` : ''}{item.scenario ? ` · what-if "${item.scenario}"` : ''}</li>)}</ul>
        : <p>Nothing is planned. Tell Maipa about cash you expect or intend and it will ask you to add it.</p>}</section>
    <p className="forecast-note">Figures from the books use posted entries, entries awaiting your approval and recurring entries. Sales not yet invoiced and bills not yet entered are not included{plan ? '; planned items are your own expectations, not records' : ''}.</p>
  </div>;
}

export function LedgerForecast({ userId, bookId, from, scenario }: { userId: string; bookId: string; from: string; scenario: string | null }) {
  const live = useLive(signal => readForecast(userId, bookId, from, scenario, signal), `${userId}:${bookId}:${from}:${scenario ?? ''}:forecast`);
  return <Frame label="Cash forecast" phase={live.phase} reload={live.reload}>{live.value && <ForecastView forecast={live.value} />}</Frame>;
}
