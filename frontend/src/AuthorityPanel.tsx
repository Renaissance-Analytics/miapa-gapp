import { useEffect, useState } from 'react';
import { Button, Input } from '@particle-academy/react-fancy';
import { readAuthority, revokeAuthority, type Authority, type Grant } from './authority.js';

const labels: Record<string, string> = {
  'ledger.post': 'Post journals', 'period.reopen': 'Reopen periods', 'vendor.payment_details.change': 'Change vendor payment details',
  'payment.release': 'Release payments', 'filing.submit': 'Submit filings', 'report.release': 'Release reports',
};
const actionName = (action: string) => labels[action] ?? `Workflow · ${action.slice('workflow.'.length)}`;

function GrantRecord({ grant, changed }: { grant: Grant; changed: () => void }) {
  const [reason, setReason] = useState('');
  const [pending, setPending] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [message, setMessage] = useState('');
  return <li className="authority-grant" data-grant-id={grant.id}>
    <strong>{actionName(grant.action)} · {grant.duty}</strong>
    <p>{grant.current ? 'Current' : grant.revoked_at ? 'Revoked' : 'Inactive'} · {grant.actor_kind === 'agent' ? 'Registered agent' : 'Your account'}
      {grant.parent_id ? ' · Delegated authority' : ' · Recorded by local operator'}</p>
    {grant.limit !== null && <p>Materiality limit: {grant.limit} {grant.currency}</p>}
    <p>Valid from {new Date(grant.starts_at).toLocaleString()} until {new Date(grant.expires_at).toLocaleString()} (exclusive)</p>
    <p>Authority reference: {grant.evidence}</p>
    {grant.current && <form className="intake-form" aria-label={`Revoke ${actionName(grant.action)} authority`} onSubmit={async event => {
      event.preventDefault(); if (pending || uncertain) return;
      setPending(true); setMessage('');
      const result = await revokeAuthority(grant.id, reason.trim());
      if (result.kind === 'ok') changed();
      else { setUncertain(true); setMessage('We could not confirm the result. Reload authority before continuing.'); }
      setPending(false);
    }}>
      <Input id={`authority-reason-${grant.id}`} label="Reason for revocation" required maxLength={256} value={reason} onValueChange={setReason} disabled={pending || uncertain} />
      <Button type="submit" variant="ghost" disabled={pending || uncertain}>Revoke authority</Button>
    </form>}
    {message && <p role="alert">{message}</p>}
  </li>;
}

function AuthorityRecords({ entityId }: { entityId: string }) {
  const [value, setValue] = useState<Authority>();
  const [attempt, setAttempt] = useState(0);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState('');
  useEffect(() => {
    let active = true; setValue(undefined); setPending(true); setMessage('');
    void readAuthority(entityId).then(result => {
      if (!active) return;
      if (result.kind === 'ok') setValue(result.value);
      else setMessage('Authority is unavailable. Reload to check your current access.');
      setPending(false);
    });
    return () => { active = false; };
  }, [entityId, attempt]);
  return <div aria-busy={pending}>
    <Button variant="ghost" disabled={pending} onClick={() => setAttempt(n => n+1)}>Reload authority</Button>
    {message && <p role="alert">{message}</p>}
    {pending && <p role="status">Checking recorded authority…</p>}
    {value && <>
      <p>Policy version {value.policy_version}. Financial execution is not yet available.</p>
      {!value.grants.length && <p>No financial or workflow authority is recorded for your account on this client.</p>}
      <ul className="authority-grants">{value.grants.map(grant => <GrantRecord key={`${grant.id}:${attempt}`} grant={grant} changed={() => setAttempt(n => n+1)} />)}</ul>
      {value.next_before && <Button variant="ghost" disabled={pending} onClick={async () => {
        setPending(true); setMessage('');
        const result = await readAuthority(entityId, value.next_before ?? undefined);
        if (result.kind === 'ok' && result.value.policy_version === value.policy_version) {
          setValue({ ...result.value, grants: [...value.grants, ...result.value.grants] });
        } else setMessage('Authority changed or could not be checked. Reload before continuing.');
        setPending(false);
      }}>Older authority records</Button>}
    </>}
  </div>;
}

export function AuthorityPanel({ entityId }: { entityId: string }) {
  const [open, setOpen] = useState(false);
  return <section className="authority-panel" aria-label="Client authority">
    <Button variant="ghost" aria-expanded={open} onClick={() => setOpen(current => !current)}>Review authority</Button>
    {open && <div><h4>Recorded authority for this client</h4>
      <p>Workspace roles and client contacts do not grant financial approval. Each action needs current, explicit authority.</p>
      <AuthorityRecords entityId={entityId} />
    </div>}
  </section>;
}
