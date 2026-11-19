import { useEffect, useState, type ReactNode } from 'react';
import { Button, Callout, Table } from '@particle-academy/react-fancy';
import { displayMoney, displaySigned, readAccountActivity, readLedger, type AccountActivity, type LedgerSnapshot } from './bookkeeping-client.js';

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
      <Table.Cell>{account.code} · {account.name}</Table.Cell><Table.Cell>{account.category}</Table.Cell>
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
