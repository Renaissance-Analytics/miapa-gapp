import { csrfToken } from './api.js';

export const ACCESS_ROLES = ['administrator', 'preparer', 'reviewer', 'viewer'] as const;
export type AccessRole = typeof ACCESS_ROLES[number];
export type LocalUser = { id: string; username: string; role: AccessRole; enabled: boolean; active: boolean };
export type AccessEvent = { actor_id: string; subject_id: string; kind: 'created' | 'changed'; role: AccessRole;
  enabled: boolean; revision: number; at: string };
export type AccessSnapshot = { actor_id: string; installation_id: string; revision: number; users: LocalUser[]; events: AccessEvent[] };
export type AccessFailure = 'invalid' | 'denied' | 'conflict' | 'unavailable';
export type AccessResult<T> = { kind: 'ok'; value: T } | { kind: AccessFailure };
export type NewLocalUser = { username: string; password: string; confirmation: string; role: AccessRole };
const options = (): RequestInit => ({ credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(10_000) });
const record = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const exact = (v: unknown, fields: string): v is Record<string, unknown> => record(v) && Object.keys(v).sort().join(',') === fields;
const id = (v: unknown): v is string => typeof v === 'string' && /^[1-9][0-9]{0,18}$/.test(v);
const uuid = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(v);
const revision = (v: unknown): v is number => Number.isInteger(v) && Number(v) >= 0 && Number(v) < 2_000_000_000;
const role = (v: unknown): v is AccessRole => typeof v === 'string' && ACCESS_ROLES.includes(v as AccessRole);

async function json(response: Response, limit = 262_144): Promise<unknown> {
  if (response.headers.get('Content-Type')?.split(';')[0].trim() !== 'application/json') throw Error('Access unavailable');
  const reader = response.body?.getReader();
  if (!reader) throw Error('Access unavailable');
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) throw Error('Access unavailable');
      chunks.push(value);
    }
  } finally { await reader.cancel(); }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
}

function snapshot(value: unknown, actorId: string): value is AccessSnapshot {
  if (!exact(value, 'actor_id,events,installation_id,revision,users') || !id(actorId) || value.actor_id !== actorId
    || !uuid(value.installation_id) || !revision(value.revision) || !Array.isArray(value.users)
    || value.users.length > 200 || !Array.isArray(value.events) || value.events.length > 20) return false;
  const ids = new Set<string>();
  for (const row of value.users) {
    if (!exact(row, 'active,enabled,id,role,username') || !id(row.id) || ids.has(row.id) || !role(row.role)
      || typeof row.enabled !== 'boolean' || typeof row.active !== 'boolean' || typeof row.username !== 'string'
      || !row.username.length || row.username.length > 150 || /[\u0000-\u001f]/.test(row.username)) return false;
    ids.add(row.id);
  }
  if (!value.users.some(row => row.id === actorId && row.role === 'administrator' && row.enabled && row.active)) return false;
  let before = value.revision + 1;
  for (const event of value.events) {
    if (!exact(event, 'actor_id,at,enabled,kind,revision,role,subject_id') || !id(event.actor_id) || !id(event.subject_id)
      || !role(event.role) || typeof event.enabled !== 'boolean' || !['created', 'changed'].includes(String(event.kind))
      || !revision(event.revision) || event.revision < 1 || event.revision >= before
      || typeof event.at !== 'string' || !/^\d{4}-\d\d-\d\dT[0-9:.+Z-]{8,32}$/.test(event.at)) return false;
    before = event.revision;
  }
  return true;
}

export async function readAdminUsers(actorId: string, fetcher: typeof fetch = fetch): Promise<AccessResult<AccessSnapshot>> {
  try {
    if (!id(actorId)) return { kind: 'unavailable' };
    const response = await fetcher('/api/admin/users/', options());
    if ([401, 403].includes(response.status)) return { kind: 'denied' };
    if (response.status !== 200) return { kind: 'unavailable' };
    const value = await json(response);
    return snapshot(value, actorId) ? { kind: 'ok', value } : { kind: 'unavailable' };
  } catch { return { kind: 'unavailable' }; }
}

export async function canAdminister(fetcher: typeof fetch = fetch): Promise<boolean> {
  try {
    const response = await fetcher('/api/workspace/', options());
    if (response.status !== 200) return false;
    const value = await json(response, 4096);
    return exact(value, 'installation_id,permissions,role') && uuid(value.installation_id)
      && value.role === 'administrator' && Array.isArray(value.permissions) && value.permissions.length <= 32
      && value.permissions.every(item => typeof item === 'string' && item.length <= 80);
  } catch { return false; }
}

export async function saveLocalUser(previous: AccessSnapshot, target: string | null,
  choice: NewLocalUser | Pick<LocalUser, 'role' | 'enabled'>, fetcher: typeof fetch = fetch): Promise<AccessResult<number>> {
  try {
    if (!snapshot(previous, previous.actor_id) || (target !== null && !id(target)) || !role(choice.role)) return { kind: 'unavailable' };
    const fields: unknown = choice;
    if (target === null) {
      if (!exact(fields, 'confirmation,password,role,username') || typeof fields.username !== 'string'
        || !fields.username.length || fields.username.length > 150 || typeof fields.password !== 'string'
        || fields.password.length < 12 || fields.password.length > 1024 || fields.confirmation !== fields.password) return { kind: 'unavailable' };
    } else if (!exact(fields, 'enabled,role') || typeof fields.enabled !== 'boolean') return { kind: 'unavailable' };
    const token = await csrfToken(fetcher);
    const response = await fetcher(target === null ? '/api/admin/users/' : `/api/admin/users/${target}/`, {
      ...options(), method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token, 'X-Maipa-Admin': previous.actor_id },
      body: JSON.stringify({ expected_revision: previous.revision, installation_id: previous.installation_id, ...choice }) });
    if ([401, 403].includes(response.status)) return { kind: 'denied' };
    if ([404, 409].includes(response.status)) return { kind: 'conflict' };
    if (response.status === 400) return { kind: 'invalid' };
    if (response.status !== (target === null ? 201 : 200)) return { kind: 'unavailable' };
    const value = await json(response, 1024);
    return exact(value, 'revision,status') && value.status === 'saved' && value.revision === previous.revision + 1
      ? { kind: 'ok', value: value.revision as number } : { kind: 'unavailable' };
  } catch { return { kind: 'unavailable' }; }
}
