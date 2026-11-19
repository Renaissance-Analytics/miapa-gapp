import { useEffect, useRef, useState } from 'react';
import { Button, Callout, ChatMessage, Text } from '@particle-academy/react-fancy';
import { approvalGroups, approvalMessage, decideApproval, decideApprovalBatch, readApprovals, type ApprovalList, type ApprovalRequest } from './approval-client.js';

const FAILURES: Record<string, string> = { ledger_review_conflict: 'The entry changed after Maipa asked.', ledger_revision_conflict: 'The books changed at the same moment.',
  ledger_date_outside_period: 'The date is outside the books’ period.', book_already_initialized: 'These books already exist.',
  ledger_account_exists: 'That account code already exists.', setup_review_conflict: 'Your setup changed after Maipa asked.' };

/** What Maipa wants to change, as approval cards. Nothing here runs until the person decides. */
export function Approvals({ userId, syncKey = 0, onDecided }: { userId: string; syncKey?: number; onDecided?: (done: ApprovalRequest[]) => void }) {
  const [list, setList] = useState<ApprovalList>();
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [status, setStatus] = useState('');
  const life = useRef(new AbortController()), pending = useRef(false);
  async function load(signal: AbortSignal) { const value = await readApprovals(userId, signal); if (!signal.aborted) { setList(value); setError(''); } }
  useEffect(() => {
    const lifetime = new AbortController(); life.current = lifetime; setList(undefined);
    void load(lifetime.signal).catch(() => {});
    const timer = setInterval(() => { if (!pending.current && document.visibilityState === 'visible') void load(lifetime.signal).catch(() => {}); }, 3000);
    return () => { lifetime.abort(); clearInterval(timer); };
  }, [userId]);
  // An agent turn may have added requests: refresh as the conversation changes.
  useEffect(() => { if (!pending.current) void load(life.current.signal).catch(() => {}); }, [syncKey]);

  async function act(run: (signal: AbortSignal) => Promise<ApprovalRequest[]>) {
    if (pending.current) return;
    pending.current = true; setBusy(true); setError(''); setStatus('');
    const signal = life.current.signal;
    try {
      const done = await run(signal);
      onDecided?.(done);
      const failed = done.filter(r => r.state === 'failed'), approved = done.filter(r => r.state === 'approved').length, rejected = done.filter(r => r.state === 'rejected').length;
      setStatus([approved && `${approved} approved and done`, rejected && `${rejected} rejected; nothing changed`,
        failed.length && `${failed.length} could not be done: ${failed.map(r => FAILURES[r.error ?? ''] ?? 'the records no longer match the request').join(' ')}`].filter(Boolean).join('. ') + '.');
    } catch (problem) { if (!signal.aborted) setError(problem instanceof Error ? problem.message : 'Approvals unavailable.'); }
    finally { if (!signal.aborted) { await load(signal).catch(() => {}); pending.current = false; setBusy(false); } }
  }
  if (userId !== list?.actor_id || (!list.pending.length && !status && !error)) return null;
  const groups = approvalGroups(list.pending);
  return <section className="approvals" aria-label="Changes waiting for your approval" aria-busy={busy} data-approvals-pending={list.pending.length}>
    {!!list.pending.length && <Text size="sm" weight="semibold">Maipa is waiting for your approval · {list.pending.length} {list.pending.length === 1 ? 'change' : 'changes'}</Text>}
    {error && <Callout color="amber" role="alert">{error}</Callout>}
    {status && <Text size="sm" role="status">{status}</Text>}
    {groups.map(group => <div className="approval-group" key={group[0].batch_id ?? group[0].id} data-approval-batch={group[0].batch_id ?? undefined}>
      {group.length > 1 && <div className="approval-batch-actions">
        <Text size="sm">{group.length} related changes</Text>
        <Button size="sm" disabled={busy} onClick={() => void act(signal => decideApprovalBatch(userId, group, 'approve', signal))}>Approve all {group.length}</Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => void act(signal => decideApprovalBatch(userId, group, 'reject', signal))}>Reject all</Button>
      </div>}
      {group.map(item => <ChatMessage key={item.id} message={approvalMessage(item)}
        onProposalAccept={() => act(async signal => [await decideApproval(userId, item, 'approve', signal)])}
        onProposalReject={() => act(async signal => [await decideApproval(userId, item, 'reject', signal)])} />)}
    </div>)}
  </section>;
}
