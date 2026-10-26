import { csrfToken } from './api.js';

export type CatalogFailure = 'invalid' | 'denied' | 'conflict' | 'unavailable';
export type CatalogResult<T> = { kind: 'ok'; value: T } | { kind: CatalogFailure };
export type CatalogSnapshot = { actor_id: string; identity: string; installation_id: string; revision: number;
  engagements: { id: string; name: string; entity_count: number }[]; selected_engagement_id: string | null;
  entities: { id: string; name: string }[]; events: CatalogEvent[] };
export type CatalogAction = 'create_engagement' | 'create_entity' | 'rename_engagement' | 'rename_entity';
export type CatalogEvent = { action: CatalogAction; target_id: string; engagement_id: string; revision: number; at: string };
export type CatalogCommand = { action: 'create_engagement'; name: string; entity_name: string }
  | { action: 'create_entity' | 'rename_engagement'; engagement_id: string; name: string }
  | { action: 'rename_entity'; entity_id: string; name: string };
export type CatalogReceipt = { status: 'saved'; actor_id: string; identity: string; installation_id: string;
  revision: number; action: CatalogAction; target_id: string; engagement_id: string };

const record = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const exact = (v: unknown, fields: string): v is Record<string, unknown> => record(v) && Object.keys(v).sort().join(',') === fields;
const actor = (v: unknown): v is string => typeof v === 'string' && /^[1-9][0-9]{0,18}$/.test(v);
const uuid = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(v);
const revision = (v: unknown): v is number => Number.isInteger(v) && Number(v) >= 0 && Number(v) < 2_000_000_000;
const name = (v: unknown): v is string => typeof v === 'string' && !!v.trim() && v.length <= 128 && !/[\u0000-\u001f\u007f]/.test(v);
const action = (v: unknown): v is CatalogAction => ['create_engagement', 'create_entity', 'rename_engagement', 'rename_entity'].includes(String(v));
const options = (): RequestInit => ({ credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(10_000) });

async function json(response: Response, budget = 262_144): Promise<unknown> {
  if (response.headers.get('Content-Type')?.split(';')[0].trim() !== 'application/json') throw Error('Client setup unavailable');
  const reader = response.body?.getReader();
  if (!reader) throw Error('Client setup unavailable');
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > budget) throw Error('Client setup unavailable');
      chunks.push(value);
    }
  } finally { await reader.cancel(); }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
}

function snapshot(v: unknown, actorId: string, selected?: string): v is CatalogSnapshot {
  if (!exact(v, 'actor_id,engagements,entities,events,identity,installation_id,revision,selected_engagement_id') || !actor(actorId)
    || v.actor_id !== actorId || !uuid(v.identity) || !uuid(v.installation_id) || !revision(v.revision)
    || !Array.isArray(v.engagements) || v.engagements.length > 100 || !Array.isArray(v.entities) || v.entities.length > 100
    || !Array.isArray(v.events) || v.events.length !== Math.min(v.revision, 20)) return false;
  const groups = new Set<string>(), entities = new Set<string>();
  for (const row of v.engagements) {
    if (!exact(row, 'entity_count,id,name') || !uuid(row.id) || groups.has(row.id) || !name(row.name)
      || !Number.isInteger(row.entity_count) || Number(row.entity_count) < 0 || Number(row.entity_count) > 100) return false;
    groups.add(row.id);
  }
  if (v.engagements.length === 0) {
    if (v.selected_engagement_id !== null || v.entities.length !== 0 || selected !== undefined) return false;
  } else if (!uuid(v.selected_engagement_id) || !groups.has(v.selected_engagement_id)
    || (selected !== undefined && v.selected_engagement_id !== selected)
    || v.engagements.find(row => row.id === v.selected_engagement_id)?.entity_count !== v.entities.length) return false;
  for (const row of v.entities) {
    if (!exact(row, 'id,name') || !uuid(row.id) || entities.has(row.id) || !name(row.name)) return false;
    entities.add(row.id);
  }
  let before = v.revision+1;
  for (const row of v.events) {
    if (!exact(row, 'action,at,engagement_id,revision,target_id') || !action(row.action) || !uuid(row.target_id)
      || !uuid(row.engagement_id) || !groups.has(row.engagement_id) || !revision(row.revision) || row.revision !== before-1
      || typeof row.at !== 'string' || !/^\d{4}-\d\d-\d\dT[0-9:.+Z-]{8,32}$/.test(row.at)
      || (row.action.endsWith('engagement') && row.target_id !== row.engagement_id)) return false;
    before = row.revision;
  }
  return true;
}

function commandAllowed(previous: CatalogSnapshot, choice: unknown): choice is CatalogCommand {
  if (!record(choice) || !name(choice.name) || !action(choice.action)) return false;
  if (choice.action === 'create_engagement') return exact(choice, 'action,entity_name,name') && name(choice.entity_name)
    && previous.engagements.length < 100;
  if (choice.action === 'rename_entity') return exact(choice, 'action,entity_id,name') && uuid(choice.entity_id)
    && previous.entities.some(row => row.id === choice.entity_id);
  return exact(choice, 'action,engagement_id,name') && uuid(choice.engagement_id)
    && previous.engagements.some(row => row.id === choice.engagement_id && (choice.action !== 'create_entity' || row.entity_count < 100));
}

export async function readClientCatalog(actorId: string, selected?: string, fetcher: typeof fetch = fetch): Promise<CatalogResult<CatalogSnapshot>> {
  try {
    if (!actor(actorId) || (selected !== undefined && !uuid(selected))) return { kind: 'unavailable' };
    const response = await fetcher('/api/intake/catalog/' + (selected ? `?engagement_id=${selected}` : ''), options());
    if ([401, 403].includes(response.status)) return { kind: 'denied' };
    if ([404, 409].includes(response.status)) return { kind: 'conflict' };
    if (response.status !== 200) return { kind: 'unavailable' };
    const value = await json(response);
    return snapshot(value, actorId, selected) ? { kind: 'ok', value } : { kind: 'unavailable' };
  } catch { return { kind: 'unavailable' }; }
}

export async function saveClientCatalog(previous: CatalogSnapshot, command: CatalogCommand, fetcher: typeof fetch = fetch): Promise<CatalogResult<CatalogReceipt>> {
  try {
    if (!snapshot(previous, previous.actor_id) || !commandAllowed(previous, command)) return { kind: 'unavailable' };
    const token = await csrfToken(fetcher);
    const response = await fetcher('/api/intake/catalog/', { ...options(), method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token, 'X-Maipa-Client-Actor': previous.actor_id },
      body: JSON.stringify({ identity: previous.identity, installation_id: previous.installation_id,
        expected_revision: previous.revision, ...command }) });
    if ([401, 403].includes(response.status)) return { kind: 'denied' };
    if ([404, 409].includes(response.status)) return { kind: 'conflict' };
    if ([400, 413].includes(response.status)) return { kind: 'invalid' };
    if (response.status !== (command.action.startsWith('create_') ? 201 : 200)) return { kind: 'unavailable' };
    const value = await json(response, 2048);
    if (!exact(value, 'action,actor_id,engagement_id,identity,installation_id,revision,status,target_id')
      || value.status !== 'saved' || value.actor_id !== previous.actor_id || value.identity !== previous.identity
      || value.installation_id !== previous.installation_id || value.revision !== previous.revision+1
      || value.action !== command.action || !uuid(value.target_id) || !uuid(value.engagement_id)) return { kind: 'unavailable' };
    if (command.action === 'create_engagement') {
      if (value.target_id !== value.engagement_id || previous.engagements.some(row => row.id === value.target_id)) return { kind: 'unavailable' };
    } else if (command.action === 'rename_entity') {
      if (value.target_id !== command.entity_id || value.engagement_id !== previous.selected_engagement_id) return { kind: 'unavailable' };
    } else {
      if (value.engagement_id !== command.engagement_id || (command.action === 'rename_engagement' && value.target_id !== command.engagement_id)
        || (command.action === 'create_entity' && previous.entities.some(row => row.id === value.target_id))) return { kind: 'unavailable' };
    }
    return { kind: 'ok', value: value as CatalogReceipt };
  } catch { return { kind: 'unavailable' }; }
}

export async function canManageClients(fetcher: typeof fetch = fetch): Promise<boolean> {
  try {
    const response = await fetcher('/api/workspace/', options());
    if (response.status !== 200) return false;
    const value = await json(response, 4096);
    return exact(value, 'installation_id,permissions,role') && uuid(value.installation_id)
      && ['administrator', 'preparer'].includes(String(value.role)) && Array.isArray(value.permissions)
      && value.permissions.length <= 32 && value.permissions.every(item => typeof item === 'string' && item.length <= 80);
  } catch { return false; }
}
