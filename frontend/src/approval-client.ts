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
const OPERATIONS = ['books.create', 'account.create', 'statement.import', 'journal.post', 'journal.reverse', 'opening.balance', 'period.close', 'period.reopen', 'setup.confirm'];
const TITLES: Record<string, string> = { 'books.create': 'Create books', 'account.create': 'Add an account',
  'statement.import': 'Import a bank statement', 'journal.post': 'Post a journal entry', 'journal.reverse': 'Reverse a posted entry', 'opening.balance': 'Record opening balances', 'period.close': 'Close the books', 'period.reopen': 'Reopen the books', 'setup.confirm': 'Save your account setup' };

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

const cell = (v: unknown) => String(v ?? '').replace(/[|\\`*_<>\[\]]/g, ' ').slice(0, 200);
/** The approval card's body: the agent's summary plus facts the server read about the target. */
export function approvalContent(item: ApprovalRequest): string {
  const lines = [cell(item.summary)];
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
  } else if (item.operation === 'account.create') {
    lines.push('', `${cell(p.code)} ${cell(p.name)} (${cell(p.category)}) in ${cell(d?.books)} books`);
  } else if (item.operation === 'statement.import') {
    lines.push('', `${cell(p.filename)} · ${cell(p.content_bytes)} bytes into ${cell(d?.books)} books. Rows become pending transactions; no entries are posted.`);
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
