export type Grant = { id: string; action: string; duty: 'preparer' | 'reviewer' | 'approver' | 'executor';
  limit: string | null; currency: string | null; starts_at: string; expires_at: string; evidence: string;
  parent_id: string | null; can_delegate: boolean; current: boolean; revoked_at: string | null; actor_kind: 'human' | 'agent' };
export type Authority = { entity_id: string; policy_version: number; financial_execution_available: false;
  grants: Grant[]; next_before: string | null };
export type Failure = 'signed_out' | 'denied' | 'conflict' | 'unavailable';
type Result<T> = { kind: 'ok'; value: T } | { kind: Failure };
const identity = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value);
const monetary = (value: unknown): value is string => typeof value === 'string' && /^(?:0|[1-9][0-9]{0,13})(?:\.[0-9]{1,4})?$/.test(value);
function expect(value: unknown): asserts value { if (!value) throw Error('Authority unavailable'); }
function object(value: unknown): Record<string, unknown> {
  expect(typeof value === 'object' && value !== null && !Array.isArray(value)); return value as Record<string, unknown>;
}
function text(value: unknown, max: number): string { expect(typeof value === 'string' && value.length > 0 && value.length <= max); return value; }
function instant(value: unknown): string {
  const result = text(value, 40); expect(/(?:Z|[+-]\d\d:\d\d)$/.test(result) && Number.isFinite(Date.parse(result))); return result;
}
const actions = new Set(['ledger.post', 'period.reopen', 'vendor.payment_details.change', 'payment.release', 'filing.submit', 'report.release',
  ...['discover', 'create', 'edit', 'triage', 'run', 'resume', 'retry', 'cancel'].map(verb => 'workflow.'+verb)]);
function grant(input: unknown): Grant {
  const row = object(input);
  expect(identity(row.id) && (row.parent_id === null || identity(row.parent_id)));
  expect(typeof row.action === 'string' && actions.has(row.action));
  expect(['preparer', 'reviewer', 'approver', 'executor'].includes(String(row.duty)));
  expect(row.actor_kind === 'human' || row.actor_kind === 'agent');
  expect(typeof row.current === 'boolean' && typeof row.can_delegate === 'boolean');
  expect(row.limit === null ? row.currency === null : monetary(row.limit) && typeof row.currency === 'string' && /^[A-Z]{3}$/.test(row.currency));
  const starts_at = instant(row.starts_at), expires_at = instant(row.expires_at);
  expect(Date.parse(starts_at) < Date.parse(expires_at));
  return { id: row.id, action: row.action, duty: row.duty as Grant['duty'], limit: row.limit as string | null,
    currency: row.currency as string | null, starts_at, expires_at, evidence: text(row.evidence, 256), parent_id: row.parent_id,
    can_delegate: row.can_delegate, current: row.current, revoked_at: row.revoked_at === null ? null : instant(row.revoked_at), actor_kind: row.actor_kind };
}
function summary(input: unknown, entityId: string): Authority {
  const row = object(input);
  expect(row.entity_id === entityId && row.financial_execution_available === false);
  expect(Number.isSafeInteger(row.policy_version) && Number(row.policy_version) >= 0);
  expect(Array.isArray(row.grants) && row.grants.length <= 50 && (row.next_before === null || identity(row.next_before)));
  return { entity_id: entityId, policy_version: Number(row.policy_version), financial_execution_available: false,
    grants: row.grants.map(grant), next_before: row.next_before };
}
function failed(status: number): Failure {
  return status === 401 ? 'signed_out' : status === 403 || status === 404 ? 'denied' : status === 409 ? 'conflict' : 'unavailable';
}
function options(): RequestInit {
  return { credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(15000), headers: { Accept: 'application/json' } };
}
async function bounded(response: Response): Promise<unknown> {
  if (!response.body) throw Error('Authority unavailable');
  const reader = response.body.getReader(); const parts: Uint8Array[] = []; let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read(); if (done) break;
      size += value.byteLength; expect(size <= 131072); parts.push(value);
    }
    const bytes = new Uint8Array(size); let offset = 0;
    for (const part of parts) { bytes.set(part, offset); offset += part.byteLength; }
    return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
  } finally { await reader.cancel(); }
}
export async function readAuthority(entityId: string, before?: string, fetcher: typeof fetch = fetch): Promise<Result<Authority>> {
  try {
    expect(identity(entityId) && (before === undefined || identity(before)));
    const response = await fetcher(`/api/policies/entities/${entityId}/${before ? '?before='+before : ''}`, options());
    if (!response.ok) return { kind: failed(response.status) };
    return { kind: 'ok', value: summary(await bounded(response), entityId) };
  } catch { return { kind: 'unavailable' }; }
}
export async function revokeAuthority(grantId: string, reason: string, fetcher: typeof fetch = fetch): Promise<{ kind: 'ok' } | { kind: Failure }> {
  try {
    expect(identity(grantId) && reason.length > 0 && reason.length <= 256);
    const csrf = await fetcher('/api/session/csrf/', options());
    if (!csrf.ok) return { kind: failed(csrf.status) };
    const token = object(await bounded(csrf)).csrf_token;
    expect(typeof token === 'string' && /^[A-Za-z0-9]{64}$/.test(token));
    const response = await fetcher(`/api/policies/grants/${grantId}/revoke/`, { ...options(), method: 'POST',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'X-CSRFToken': token }, body: JSON.stringify({ reason }) });
    if (!response.ok) return { kind: failed(response.status) };
    expect(object(await bounded(response)).status === 'revoked');
    return { kind: 'ok' };
  } catch { return { kind: 'unavailable' }; }
}
