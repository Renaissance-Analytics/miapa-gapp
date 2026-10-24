export const FACT_LABELS = {
  legal_form: 'Legal form', tax_classification: 'Tax classification', tax_elections: 'Tax elections',
  ownership: 'Ownership', periods: 'Reporting periods', currency: 'Currency', reporting_basis: 'Reporting basis',
  jurisdictions: 'Jurisdictions', industry: 'Industry', systems: 'Source systems', records_quality: 'Records quality',
  records_complete: 'Records complete', authorized_contacts: 'Authorized contacts', engagement_scope: 'Engagement scope',
} as const;
export type FactKey = keyof typeof FACT_LABELS;
export const FACT_KEYS = Object.keys(FACT_LABELS) as FactKey[];
export const CHOICES: Partial<Record<FactKey, string[]>> = {
  legal_form: ['sole_proprietorship', 'partnership', 'llc', 'corporation', 'nonprofit', 'trust', 'other'],
  tax_classification: ['disregarded_entity', 'partnership', 'c_corporation', 's_corporation', 'tax_exempt', 'trust', 'other'],
  reporting_basis: ['cash', 'accrual', 'other'], records_quality: ['complete', 'incomplete', 'unreconciled', 'needs_review'],
};
export type FactValue = string | boolean | (string | Record<string, string | boolean>)[];
export type Evidence = { reference: string; locator: string; observed_on: string };
export type FactDraft = { key: FactKey; knowledge: 'known' | 'unknown'; value: FactValue | null;
  expected_revision: number; effective_from: string; effective_to: string | null; evidence: Evidence };
export type FactState = { knowledge: 'known' | 'unknown'; value: FactValue | null; revision_id: string | null };
export type FactRevision = FactState & { id: string; revision_id: string; key: FactKey; revision: number;
  effective_from: string; effective_to: string | null; evidence: Evidence; recorded_by: string; recorded_at: string };
export type EntitySummary = { id: string; name: string; revision: number };
export type Engagement = { id: string; name: string; entities: EntitySummary[] };
export type EntityProfile = EntitySummary & { engagement_id: string; on: string; facts: Record<FactKey, FactState | FactRevision>;
  can_edit: boolean; stale_conclusions: number };
export type History = { revisions: FactRevision[]; next_before: number | null };
export type Failure = 'invalid' | 'signed_out' | 'denied' | 'conflict' | 'unavailable';
export type Result<T> = { kind: 'ok'; value: T } | { kind: Failure };

const uuid = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value);
const dateText = (value: unknown): value is string => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value);
function expect(condition: unknown): asserts condition { if (!condition) throw Error('Intake unavailable'); }
function object(value: unknown): Record<string, unknown> { expect(typeof value === 'object' && value !== null && !Array.isArray(value)); return value as Record<string, unknown>; }
function text(value: unknown, max = 128): string { expect(typeof value === 'string' && value.length > 0 && value.length <= max); return value; }
function number(value: unknown): number { expect(typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 && value <= 2_000_000_000); return value; }
function id(value: unknown): string { expect(uuid(value)); return value; }
function value(input: unknown): FactValue | null {
  if (input === null || typeof input === 'boolean' || typeof input === 'string') return input;
  expect(Array.isArray(input) && input.length <= 50);
  return input.map(item => {
    if (typeof item === 'string') return item;
    const row = object(item);
    expect(Object.keys(row).length <= 5 && Object.values(row).every(v => typeof v === 'string' || typeof v === 'boolean'));
    return row as Record<string, string | boolean>;
  });
}
function revision(input: unknown): FactRevision {
  const row = object(input), evidence = object(row.evidence);
  expect(row.knowledge === 'known' || row.knowledge === 'unknown');
  expect(typeof row.key === 'string' && FACT_KEYS.includes(row.key as FactKey));
  expect(dateText(row.effective_from) && (row.effective_to === null || dateText(row.effective_to)));
  expect(dateText(evidence.observed_on));
  expect(typeof row.recorded_by === 'string' && /^\d+$/.test(row.recorded_by));
  expect(typeof row.recorded_at === 'string' && row.recorded_at.length <= 64);
  const parsed = value(row.value);
  expect(row.knowledge === 'unknown' ? parsed === null : parsed !== null);
  return { id: id(row.id), revision_id: id(row.revision_id), key: row.key as FactKey, revision: number(row.revision),
    knowledge: row.knowledge, value: parsed, effective_from: row.effective_from, effective_to: row.effective_to,
    evidence: { reference: text(evidence.reference, 256), locator: text(evidence.locator, 512), observed_on: evidence.observed_on },
    recorded_by: row.recorded_by, recorded_at: row.recorded_at };
}
function entity(input: unknown): EntitySummary {
  const row = object(input); return { id: id(row.id), name: text(row.name), revision: number(row.revision) };
}
function engagement(input: unknown): Engagement {
  const row = object(input); expect(Array.isArray(row.entities) && row.entities.length <= 100);
  return { id: id(row.id), name: text(row.name), entities: row.entities.map(entity) };
}
function profile(input: unknown): EntityProfile {
  const row = object(input), facts = object(row.facts);
  expect(typeof row.can_edit === 'boolean' && dateText(row.on));
  const parsed = {} as Record<FactKey, FactState | FactRevision>;
  for (const key of FACT_KEYS) {
    const item = object(facts[key]);
    if (item.revision_id === null) {
      expect(item.knowledge === 'unknown' && item.value === null);
      parsed[key] = { knowledge: 'unknown', value: null, revision_id: null };
    } else { parsed[key] = revision(item); expect((parsed[key] as FactRevision).key === key); }
  }
  return { ...entity(row), engagement_id: id(row.engagement_id), on: row.on, facts: parsed,
    can_edit: row.can_edit, stale_conclusions: number(row.stale_conclusions) };
}
const options = (): RequestInit => ({ credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(10_000) });
async function request<T>(path: string, parse: (input: unknown) => T, data: object | undefined, fetcher: typeof fetch): Promise<Result<T>> {
  try {
    let init = options();
    if (data !== undefined) {
      const csrf = await fetcher('/api/session/csrf/', options());
      if (csrf.status !== 200) return { kind: 'unavailable' };
      const token = object(await csrf.json()).csrf_token;
      expect(typeof token === 'string' && /^[A-Za-z0-9]{64}$/.test(token));
      init = { ...options(), method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token }, body: JSON.stringify(data) };
    }
    const response = await fetcher(path, init);
    const states: Partial<Record<number, Failure>> = { 400: 'invalid', 401: 'signed_out', 403: 'denied', 409: 'conflict', 413: 'invalid' };
    if (states[response.status]) return { kind: states[response.status]! };
    if (response.status !== (data === undefined ? 200 : 201)) return { kind: 'unavailable' };
    return { kind: 'ok', value: parse(await response.json()) };
  } catch { return { kind: 'unavailable' }; }
}
export function factPayload(draft: FactDraft): FactDraft {
  return { key: draft.key, knowledge: draft.knowledge, value: draft.knowledge === 'unknown' ? null : draft.value,
    expected_revision: draft.expected_revision, effective_from: draft.effective_from, effective_to: draft.effective_to,
    evidence: { reference: draft.evidence.reference, locator: draft.evidence.locator, observed_on: draft.evidence.observed_on } };
}
export const label = (value: string) => value === 'llc' ? 'LLC' : value.replaceAll('_', ' ').replace(/^./, c => c.toUpperCase());
export function formatFact(fact: Pick<FactState, 'knowledge' | 'value'>): string {
  if (fact.knowledge === 'unknown') return 'Unknown';
  if (typeof fact.value === 'boolean') return fact.value ? 'Yes' : 'No';
  if (typeof fact.value === 'string') return Object.values(CHOICES).some(choices => choices.includes(fact.value as string)) ? label(fact.value) : fact.value;
  if (Array.isArray(fact.value)) return fact.value.length ? fact.value.map(item => typeof item === 'string' ? item :
    Object.entries(item).map(([key, field]) => `${label(key)}: ${typeof field === 'boolean' ? field ? 'Yes' : 'No' : field}`).join(' · ')).join('; ') : 'None recorded';
  return 'Unknown';
}
export function listEngagements(fetcher: typeof fetch = fetch): Promise<Result<Engagement[]>> {
  return request('/api/intake/engagements/', input => { const rows = object(input).engagements; expect(Array.isArray(rows) && rows.length <= 100); return rows.map(engagement); }, undefined, fetcher);
}
export function createEngagement(name: string, entityName: string, fetcher: typeof fetch = fetch): Promise<Result<Engagement>> {
  return request('/api/intake/engagements/', engagement, { name, entity_name: entityName }, fetcher);
}
export function addEntity(engagementId: string, name: string, fetcher: typeof fetch = fetch): Promise<Result<EntitySummary>> {
  if (!uuid(engagementId)) return Promise.resolve({ kind: 'unavailable' });
  return request(`/api/intake/engagements/${engagementId}/entities/`, entity, { name }, fetcher);
}
export function readEntity(entityId: string, on: string, fetcher: typeof fetch = fetch): Promise<Result<EntityProfile>> {
  if (!uuid(entityId) || !dateText(on)) return Promise.resolve({ kind: 'unavailable' });
  return request(`/api/intake/entities/${entityId}/?on=${on}`, profile, undefined, fetcher);
}
export function saveFact(entityId: string, draft: FactDraft, fetcher: typeof fetch = fetch): Promise<Result<FactRevision>> {
  if (!uuid(entityId)) return Promise.resolve({ kind: 'unavailable' });
  return request(`/api/intake/entities/${entityId}/facts/`, input => {
    const parsed = revision(input); expect(parsed.key === draft.key && parsed.revision === draft.expected_revision + 1); return parsed;
  }, factPayload(draft), fetcher);
}
export function readHistory(entityId: string, key: FactKey, before: number | undefined, fetcher: typeof fetch = fetch): Promise<Result<History>> {
  if (!uuid(entityId) || !FACT_KEYS.includes(key) || (before !== undefined && (!Number.isSafeInteger(before) || before <= 0))) return Promise.resolve({ kind: 'unavailable' });
  return request(`/api/intake/entities/${entityId}/facts/${key}/history/${before === undefined ? '' : `?before=${before}`}`, input => {
    const row = object(input); expect(Array.isArray(row.revisions) && row.revisions.length <= 50);
    return { revisions: row.revisions.map(revision), next_before: row.next_before === null ? null : number(row.next_before) };
  }, undefined, fetcher);
}
