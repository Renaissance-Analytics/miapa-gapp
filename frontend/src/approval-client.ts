import { csrfToken } from './api.js';
import type { ChatMessageData } from '@particle-academy/react-fancy';

/** One exact change the agent asked for. Only the person's decision executes it. */
export interface ApprovalRequest {
  id: string; operation: string; summary: string; payload: Record<string, unknown>; payload_hash: string;
  batch_id: string | null; state: 'pending' | 'approved' | 'rejected' | 'failed';
  detail: Record<string, unknown> | null; result: unknown; error: string | null; created_at: string; decided_at: string | null;
}
export interface ApprovalList { actor_id: string; pending: ApprovalRequest[]; recent: ApprovalRequest[] }

const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const exact = (v: Record<string, unknown>, keys: string[]) => Object.keys(v).sort().join('|') === [...keys].sort().join('|');
const uuid = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(v);
const hash = (v: unknown): v is string => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
const text = (v: unknown, max: number): v is string => typeof v === 'string' && v.length <= max;
const unavailable = () => Error('We could not confirm your pending approvals. Nothing was changed. Reload and review before trying again.');
const OPERATIONS = ['books.create', 'account.create', 'account.cash', 'statement.import', 'statement.account', 'statement.balances', 'reconciliation.record', 'statement.match', 'statement.duplicate', 'journal.post', 'journal.reverse', 'opening.balance', 'period.close', 'period.reopen', 'setup.confirm'];
const TITLES: Record<string, string> = { 'books.create': 'Create books', 'account.create': 'Add an account', 'account.cash': 'Mark a bank or cash account',
  'statement.import': 'Import a bank statement', 'journal.post': 'Post a journal entry', 'statement.account': 'Tie a statement to its bank account', 'statement.balances': 'Add a statement\'s balances', 'reconciliation.record': 'Record a bank reconciliation', 'statement.match': 'Match a statement line to an entry', 'statement.duplicate': 'Mark a repeated statement line', 'journal.reverse': 'Reverse a posted entry', 'opening.balance': 'Record opening balances', 'period.close': 'Close the books', 'period.reopen': 'Reopen the books', 'setup.confirm': 'Save your account setup' };

function request(v: unknown): v is ApprovalRequest {
  return object(v) && exact(v, ['id', 'operation', 'summary', 'payload', 'payload_hash', 'batch_id', 'state', 'detail', 'result', 'error', 'created_at', 'decided_at'])
    && uuid(v.id) && OPERATIONS.includes(String(v.operation)) && text(v.summary, 500) && !!v.summary && object(v.payload) && hash(v.payload_hash)
    && (v.batch_id === null || uuid(v.batch_id)) && ['pending', 'approved', 'rejected', 'failed'].includes(String(v.state))
    && (v.detail === null || object(v.detail)) && (v.error === null || text(v.error, 64))
    && text(v.created_at, 40) && Number.isFinite(Date.parse(String(v.created_at))) && (v.decided_at === null || text(v.decided_at, 40));
}
export function parseApprovalList(v: unknown, user: string): ApprovalList {
  if (!object(v) || !exact(v, ['actor_id', 'pending', 'recent']) || v.actor_id !== user || !Array.isArray(v.pending) || v.pending.length > 200
      || !Array.isArray(v.recent) || v.recent.length > 20 || !v.pending.every(r => request(r) && r.state === 'pending')
      || !v.recent.every(r => request(r) && r.state !== 'pending')
      || new Set(v.pending.map(r => (r as ApprovalRequest).id)).size !== v.pending.length) throw unavailable();
  return v as unknown as ApprovalList;
}

async function call(user: string, path: string, signal: AbortSignal, body: unknown | undefined, fetcher: typeof fetch) {
  if (!/^[0-9]+$/.test(user) || signal.aborted) throw unavailable();
  const headers: Record<string, string> = { 'X-Maipa-Agent-Actor': user };
  if (body !== undefined) { headers['Content-Type'] = 'application/json'; headers['X-CSRFToken'] = await csrfToken(fetcher); }
  const response = await fetcher(path, { method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
    headers, signal: AbortSignal.any([signal, AbortSignal.timeout(20000)]), ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  if (response.status !== 200 || signal.aborted) throw unavailable();
  return response.json() as Promise<unknown>;
}
export async function readApprovals(user: string, signal: AbortSignal, fetcher: typeof fetch = fetch) {
  return parseApprovalList(await call(user, '/api/approvals/', signal, undefined, fetcher), user);
}
export interface SavedAttachment { id: string; name: string; size: number; sha256: string; lines: number; created_at: string }
/** Upload one attached text file for the agent. The acknowledgment must be this exact file name and size. */
export async function uploadAttachment(user: string, name: string, content: string, signal: AbortSignal, fetcher: typeof fetch = fetch): Promise<SavedAttachment> {
  if (!/^[0-9]+$/.test(user) || signal.aborted) throw unavailable();
  const response = await fetcher('/api/agent/attachments/', { method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
    headers: { 'X-Maipa-Agent-Actor': user, 'Content-Type': 'application/json', 'X-CSRFToken': await csrfToken(fetcher) },
    signal: AbortSignal.any([signal, AbortSignal.timeout(20000)]), body: JSON.stringify({ name, content }) });
  if (response.status !== 201) throw Error('Maipa could not receive that file. Your message and file were kept; check that it is a CSV text file and try again.');
  const v: unknown = await response.json();
  if (!object(v) || !exact(v, ['id', 'name', 'size', 'sha256', 'lines', 'created_at']) || !uuid(v.id) || v.name !== name || !hash(v.sha256)
      || v.size !== new TextEncoder().encode(content).length || !Number.isSafeInteger(v.lines)) throw unavailable();
  return v as unknown as SavedAttachment;
}
/** Why the server refused a workbook, in words the person can act on. */
export function workbookProblem(code: unknown): string {
  const row = typeof code === 'string' ? /^import_row_([0-9]{1,4})_invalid$/.exec(code) : null;
  if (row) return `Row ${row[1]} of that spreadsheet is not a valid statement row. Each row needs a date as YYYY-MM-DD, a description and an amount with at most four decimals. Your message and file were kept.`;
  if (code === 'import_header_invalid') return 'The first row of that spreadsheet must be the headings date, description, amount and, optionally, reference. Your message and file were kept.';
  if (code === 'import_workbook_sheets') return 'That workbook has more than one sheet. Save the statement as a workbook with a single sheet and attach it again. Your message and file were kept.';
  if (code === 'import_row_count_invalid') return 'That spreadsheet has no statement rows, or more than 1,000. Your message and file were kept.';
  if (code === 'import_document_no_text') return 'That PDF has no readable text; it looks like a scanned image. Download the statement from your bank as a PDF with text, or as CSV or Excel. Your message and file were kept.';
  if (code === 'import_document_invalid') return 'Maipa could not read that PDF. Your message and file were kept; check that it is a statement PDF without a password, up to 700 KB and 30 pages.';
  return 'Maipa could not read that file. Your message and file were kept; check that it is an .xlsx statement up to 512 KB or a PDF up to 700 KB and try again.';
}
/** Send an Excel or PDF statement as its exact bytes. The acknowledgment must name this file and its exact size. */
export async function uploadWorkbook(user: string, name: string, bytes: Uint8Array, signal: AbortSignal, fetcher: typeof fetch = fetch): Promise<SavedAttachment> {
  if (!/^[0-9]+$/.test(user) || signal.aborted || !bytes.length || bytes.length > (/\.pdf$/i.test(name) ? 716_800 : 524_288)) throw unavailable();
  let binary = '';
  for (let start = 0; start < bytes.length; start += 0x8000) binary += String.fromCharCode(...bytes.subarray(start, start + 0x8000));
  const response = await fetcher('/api/agent/attachments/', { method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
    headers: { 'X-Maipa-Agent-Actor': user, 'Content-Type': 'application/json', 'X-CSRFToken': await csrfToken(fetcher) },
    signal: AbortSignal.any([signal, AbortSignal.timeout(20000)]), body: JSON.stringify({ name, content_base64: btoa(binary) }) });
  if (response.status !== 201) {
    let code: unknown;
    try { const refused: unknown = await response.json(); code = object(refused) && object(refused.error) ? refused.error.code : undefined; } catch { code = undefined; }
    throw Error(workbookProblem(code));
  }
  const v: unknown = await response.json();
  if (!object(v) || !exact(v, ['id', 'name', 'size', 'sha256', 'lines', 'created_at']) || !uuid(v.id) || v.name !== name || !hash(v.sha256)
      || v.size !== bytes.length || !Number.isSafeInteger(v.lines)) throw unavailable();
  return v as unknown as SavedAttachment;
}
/** Decide one request. The acknowledgment must be this request, decided, with the reviewed hash. */
export async function decideApproval(user: string, item: ApprovalRequest, decision: 'approve' | 'reject', signal: AbortSignal, fetcher: typeof fetch = fetch) {
  if (item.state !== 'pending') throw unavailable();
  const v = await call(user, `/api/approvals/${item.id}/decide/`, signal, { decision, payload_hash: item.payload_hash }, fetcher);
  if (!request(v) || v.id !== item.id || v.payload_hash !== item.payload_hash || v.state === 'pending'
      || (decision === 'reject' && v.state !== 'rejected') || (decision === 'approve' && v.state === 'rejected')) throw unavailable();
  return v;
}
/** Decide every pending request of one batch, sending exactly the hashes the person reviewed. */
export async function decideApprovalBatch(user: string, items: ApprovalRequest[], decision: 'approve' | 'reject', signal: AbortSignal, fetcher: typeof fetch = fetch) {
  const batch = items[0]?.batch_id;
  if (!batch || items.some(i => i.batch_id !== batch || i.state !== 'pending')) throw unavailable();
  const v = await call(user, `/api/approvals/batch/${batch}/decide/`, signal,
    { decision, payload_hashes: Object.fromEntries(items.map(i => [i.id, i.payload_hash])) }, fetcher);
  if (!object(v) || !exact(v, ['batch_id', 'requests']) || v.batch_id !== batch || !Array.isArray(v.requests) || !v.requests.every(request)
      || items.some(i => !(v.requests as ApprovalRequest[]).some(r => r.id === i.id && r.state !== 'pending'))) throw unavailable();
  return v.requests as ApprovalRequest[];
}

const cell = (v: unknown, limit = 200) => String(v ?? '').replace(/[|\\`*_<>\[\]]/g, ' ').slice(0, limit);
/** The approval card's body: the agent's summary plus facts the server read about the target. */
export function approvalContent(item: ApprovalRequest): string {
  // The server accepts summaries up to 500 characters; show all of it.
  const lines = [cell(item.summary, 500)];
  const d = item.detail, p = item.payload;
  if ((item.operation === 'journal.post' || item.operation === 'journal.reverse') && d && Array.isArray(d.lines)) {
    lines.push('', `${cell(d.date)} · ${cell(d.description)} · ${cell(d.currency)}`, '', '| Account | Debit | Credit |', '|---|---:|---:|',
      ...d.lines.filter(object).map(l => `| ${cell(l.account)} | ${cell(l.debit)} | ${cell(l.credit)} |`));
    if (item.operation === 'journal.reverse') lines.push('', `This posts the exact opposite of the entry dated ${cell(d.original_date)}. The original entry stays in your books.`);
    if (d.current === false) lines.push('', 'This entry changed after Maipa asked. Approving will fail; ask Maipa to prepare it again.');
  } else if (item.operation === 'opening.balance') {
    if (d && Array.isArray(d.lines)) {
      const rows = d.lines.filter(object);
      lines.push('', `${cell(d.books)} books · starting ${cell(d.date)} · ${cell(d.currency)}`, '', '| Account | Debit | Credit |', '|---|---:|---:|',
        ...rows.map(l => `| ${cell(l.account)} | ${cell(l.debit)} | ${cell(l.credit)} |`),
        `| **Total** | ${cell(d.total_debit)} | ${cell(d.total_credit)} |`);
      const offset = rows.find(l => l.offset === true);
      lines.push('', `Your figures total ${cell(d.control_debit)} in debits and ${cell(d.control_credit)} in credits.`
        + (offset ? ` The difference goes to ${cell(offset.account)} so the entry balances.` : ' They balance, so nothing goes to opening balance equity.'));
    } else lines.push('', 'These opening balances can no longer be recorded as asked. Approving will fail; ask Maipa to prepare them again.');
  } else if (item.operation === 'period.close') {
    lines.push('', `Close ${cell(d?.books)} books through ${cell(p.through)}. Entries on or before that date can no longer be added, posted or reversed into. Posted history is not changed.`);
  } else if (item.operation === 'period.reopen') {
    lines.push('', p.through === null ? `Reopen ${cell(d?.books)} books fully (currently closed through ${cell(d?.closed_through)}).`
      : `Move the close of ${cell(d?.books)} books back to ${cell(p.through)} (currently ${cell(d?.closed_through)}).`);
  } else if (item.operation === 'books.create') {
    lines.push('', `${cell(d?.entity)} · ${cell(p.currency)} · ${cell(p.period_start)} to ${cell(p.period_end)}`);
  } else if (item.operation === 'statement.balances') {
    lines.push('', d ? `${cell(d.statement)} · ${cell(d.account ?? 'no bank account yet')} · ${cell(d.books)} books`
      + `\n\nOpening balance ${cell(d.opening_balance)} + ${cell(d.row_count)} ${Number(d.row_count) === 1 ? 'line' : 'lines'} ${cell(d.net)} = closing balance ${cell(d.closing_balance)} (${cell(d.currency)}). `
      + 'These are recorded as the balances printed on the statement. No entries or amounts change.'
      + (d.current === false ? ' This statement already has balances. Approving will fail; ask Maipa to check again.' : '')
      : 'This statement can no longer be found. Approving will fail; ask Maipa to check again.');
  } else if (item.operation === 'reconciliation.record') {
    lines.push('', d ? `${cell(d.statement)} · ${cell(d.account)} · as of ${cell(d.as_of)}`
      + `\n\n| | ${cell(d.currency)} |\n|---|---:|\n| Bank closing balance | ${cell(d.closing_balance)} |\n| Books balance | ${cell(d.book_balance)} |`
      + `\n| Statement lines not yet posted | ${cell(d.unrecorded_total)} |\n| Entries the bank has not shown | ${cell(d.outstanding_total)} |\n| Difference | ${cell(d.difference)} |`
      + (d.reconciled === true ? '\n\nThe statement agrees with the books. Approving records that you reviewed it. No entries or amounts change.'
        : '\n\nThe statement no longer agrees with the books. Approving will fail; ask Maipa to reconcile it again.')
      : 'This statement can no longer be found. Approving will fail; ask Maipa to check again.');
  } else if (item.operation === 'statement.account') {
    lines.push('', d ? `${cell(d.statement)} (${cell(d.row_count)} ${Number(d.row_count) === 1 ? 'line' : 'lines'}) in ${cell(d.books)} books will be recorded as a statement of ${cell(d.account)}. `
      + `Entries already made from its lines agree with that account. No entries or amounts change.`
      + (d.current === false ? ' This statement is already tied to an account. Approving will fail; ask Maipa to check again.' : '')
      : 'This statement or account can no longer be found. Approving will fail; ask Maipa to check again.');
  } else if (item.operation === 'statement.match') {
    const row = d && object(d.row) ? d.row : null, entry = d && object(d.entry) ? d.entry : null;
    if (row && entry && d && Array.isArray(d.lines)) {
      lines.push('', `Statement line: ${cell(row.date)} · ${cell(row.description)} · ${cell(row.amount)} ${cell(d.currency)} on ${cell(row.account)}`,
        '', `Entry #${cell(entry.sequence)} dated ${cell(entry.date)} · ${cell(entry.description)} · ${entry.state === 'posted' ? 'posted' : 'awaiting your review'}`,
        '', '| Account | Debit | Credit |', '|---|---:|---:|', ...d.lines.filter(object).map(l => `| ${cell(l.account)} | ${cell(l.debit)} | ${cell(l.credit)} |`),
        '', 'This entry already records the movement, so the line is settled without a second entry. No amounts change.');
    } else lines.push('', 'This line or entry can no longer be found. Approving will fail; ask Maipa to check again.');
  } else if (item.operation === 'statement.duplicate') {
    const row = d && object(d.row) ? d.row : null, of = d && object(d.of) ? d.of : null;
    if (row && of && d) {
      lines.push('', `${cell(d.account)} · ${cell(d.currency)}`, '', '| | Statement | Date | Description | Amount |', '|---|---|---|---|---:|',
        `| This line | ${cell(row.statement)} | ${cell(row.date)} | ${cell(row.description)} | ${cell(row.amount)} |`,
        `| Repeats | ${cell(of.statement)} | ${cell(of.date)} | ${cell(of.description)} | ${cell(of.amount)} |`,
        '', 'The line is settled as a repeat and gets no entry, so the movement is counted once. If these were two separate transactions, reject this.');
    } else lines.push('', 'These lines can no longer be found. Approving will fail; ask Maipa to check again.');
  } else if (item.operation === 'account.cash') {
    lines.push('', d ? `${cell(d.account)} in ${cell(d.books)} books will ${p.cash === true ? 'count' : 'no longer count'} as a bank account or cash on hand. No amounts change.`
      : 'This account can no longer be found. Approving will fail; ask Maipa to check again.');
  } else if (item.operation === 'account.create') {
    lines.push('', `${cell(p.code)} ${cell(p.name)} (${cell(p.category)}) in ${cell(d?.books)} books`);
  } else if (item.operation === 'statement.import') {
    lines.push('', `${cell(p.filename)} · ${cell(p.content_bytes)} bytes into ${cell(d?.books)} books${d?.account ? `, as a statement of ${cell(d.account)}` : ''}. Rows become pending transactions; no entries are posted.`);
    if (d && Array.isArray(d.rows)) {
      // A reconciled statement: the rows to be saved and the balances they add up between.
      const rows = d.rows.filter(object), more = Number(d.row_count) - rows.length;
      lines.push('', '| Date | Description | Amount |', '|---|---|---:|', ...rows.map(r => `| ${cell(r.date)} | ${cell(r.description)} | ${cell(r.amount)} |`));
      if (more > 0) lines.push('', `…and ${more} more ${more === 1 ? 'row' : 'rows'}.`);
      lines.push('', `Opening balance ${cell(d.opening_balance)} + rows ${cell(d.net)} = closing balance ${cell(d.closing_balance)} (${cell(d.currency)}). `
        + `These ${cell(d.row_count)} rows add up exactly between the statement's two balances.`);
      if (/\.pdf$/i.test(String(p.filename))) lines.push('', 'Maipa read these rows from the PDF. Check them against your statement before approving.');
    }
  }
  return lines.join('\n');
}
/** A request as a published Fancy ChatMessage approval card. */
export function approvalMessage(item: ApprovalRequest): ChatMessageData {
  return { id: `approval-${item.id}`, role: 'assistant', author: { name: 'Maipa' }, content: approvalContent(item), createdAt: item.created_at,
    proposal: { id: item.id, title: TITLES[item.operation] ?? 'Change records',
      state: item.state === 'pending' ? 'pending' : item.state === 'approved' ? 'accepted' : item.state === 'rejected' ? 'rejected' : 'withdrawn',
      acceptLabel: 'Approve', rejectLabel: 'Reject' } };
}
/** The note sent to the agent, on the person's behalf, after they decide: exact outcomes so it can carry on. */
export function decisionMessage(done: ApprovalRequest[]): string {
  const say = (state: string, label: string) => { const items = done.filter(r => r.state === state);
    return items.length ? `${label}: ${items.map(r => cell(r.summary)).join('; ')}.` : ''; };
  const parts = [say('approved', 'I approved and Maipa completed'), say('rejected', 'I rejected (nothing changed)'),
    say('failed', 'I approved but these could not be done and need to be prepared again')].filter(Boolean);
  return parts.length ? `${parts.join(' ')} Please continue with what is left.`.slice(0, 4000) : '';
}
/** Pending requests grouped so related ones can be approved together, oldest first. */
export function approvalGroups(pending: ApprovalRequest[]): ApprovalRequest[][] {
  const groups = new Map<string, ApprovalRequest[]>();
  for (const item of pending) { const key = item.batch_id ?? item.id; groups.set(key, [...(groups.get(key) ?? []), item]); }
  return [...groups.values()];
}
