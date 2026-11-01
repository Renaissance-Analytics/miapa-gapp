import { csrfToken } from './api.js';

export type FormValues = { name: string; entity_name: string };
export interface FormBinding { userId: string; accountId: string; leaseId: string; componentId: string }
export interface FormDraft { id: string; revision: number; component_id: string; kind: 'new-client'; fields: unknown[]; values: FormValues }
export interface FormState { phase: 'loading' | 'ready' | 'saving' | 'conflict' | 'unavailable' | 'closed'; saved: FormDraft | null; values: FormValues }
export type FormRequest = (method: 'GET' | 'POST', command?: { id: string; expected_revision: number; values: Partial<FormValues> }, signal?: AbortSignal) => Promise<FormDraft>;
export class SharedFormError extends Error { constructor(readonly kind: 'conflict' | 'unavailable' | 'closed') { super('Shared form unavailable'); } }
const blank = (): FormValues => ({ name: '', entity_name: '' });
const uuid = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(v);
const handle = (v: unknown): v is string => typeof v === 'string' && /^[a-z][a-z0-9-]{0,47}$/.test(v);
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const exact = (v: Record<string, unknown>, keys: string[]) => Object.keys(v).length === keys.length && keys.every(k => Object.hasOwn(v, k));
const counter = (v: unknown): v is number => Number.isSafeInteger(v) && Number(v) >= 0;
const text = (v: unknown): v is string => typeof v === 'string' && [...v].length <= 128 && !/[\u0000-\u001f\u007f]/.test(v);
const unavailable = (): never => { throw new SharedFormError('unavailable'); };

export function parseFormDraft(value: unknown, component: string): FormDraft {
  if (!object(value) || !exact(value, ['id', 'revision', 'component_id', 'kind', 'fields', 'values'])
    || !uuid(value.id) || !counter(value.revision) || !handle(component) || value.component_id !== component || value.kind !== 'new-client'
    || !Array.isArray(value.fields) || value.fields.length !== 2 || !object(value.values)
    || !exact(value.values, ['name', 'entity_name']) || !text(value.values.name) || !text(value.values.entity_name)) return unavailable();
  const fields = value.fields.map((field, index) => {
    const name = index ? 'entity_name' : 'name', label = index ? 'Initial entity name' : 'Client / engagement name';
    if (!object(field) || !exact(field, ['name', 'label', 'type', 'required', 'max_length']) || field.name !== name
      || field.label !== label || field.type !== 'text' || field.required !== true || field.max_length !== 128) return unavailable();
    return { name, label, type: 'text', required: true, max_length: 128 };
  });
  return { id: value.id, revision: value.revision, component_id: component, kind: 'new-client', fields,
    values: { name: value.values.name, entity_name: value.values.entity_name } };
}

function httpRequest(binding: FormBinding): FormRequest {
  if (!/^[0-9]+$/.test(binding.userId) || !uuid(binding.accountId) || !uuid(binding.leaseId) || !handle(binding.componentId)) return unavailable();
  return async (method, command, signal) => {
    const headers: Record<string, string> = { 'X-Maipa-Agent-Actor': binding.userId };
    if (command) { headers['Content-Type'] = 'application/json'; headers['X-CSRFToken'] = await csrfToken(fetch); }
    if (signal?.aborted) throw new SharedFormError('closed');
    const response = await fetch(`/api/agent/accounts/${binding.accountId}/ui/${binding.leaseId}/forms/${binding.componentId}/`, {
      method, headers, credentials: 'same-origin', cache: 'no-store', redirect: 'error',
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(10000)]) : AbortSignal.timeout(10000),
      ...(command ? { body: JSON.stringify(command) } : {}),
    });
    if (response.status === 409) {
      const value: unknown = await response.json();
      if (object(value) && object(value.error) && value.error.code === 'actor_changed') throw new SharedFormError('closed');
      if (object(value) && object(value.error) && value.error.code === 'draft_changed') throw new SharedFormError('conflict');
      return unavailable();
    }
    if ([401, 403, 404].includes(response.status)) throw new SharedFormError('closed');
    if (response.status !== 200) return unavailable();
    return parseFormDraft(await response.json(), binding.componentId);
  };
}

/** One controlled pending form. Persistence acknowledgment never replaces newer typing. */
export class SharedFormController {
  state: FormState = { phase: 'loading', saved: null, values: blank() };
  private readonly request: FormRequest;
  private readonly abort = new AbortController();
  private queued: Partial<FormValues> = {};
  private writing: Promise<void> | null = null;
  private reading = false;
  private generation = 0;
  constructor(readonly binding: FormBinding, readonly changed: (state: FormState) => void, request?: FormRequest) {
    this.request = request ?? httpRequest(binding);
  }
  private publish(state: FormState) { this.state = state; this.changed(state); }
  private failed(problem: unknown) {
    if (this.state.phase === 'closed') return;
    if (problem instanceof SharedFormError && problem.kind === 'closed') { this.dispose(); return; }
    this.publish({ ...this.state, phase: problem instanceof SharedFormError && problem.kind === 'conflict' ? 'conflict' : 'unavailable' });
  }
  async load(explicit = false): Promise<void> {
    if (this.state.phase === 'closed' || this.writing || this.reading || Object.keys(this.queued).length
      || (!explicit && ['conflict', 'unavailable'].includes(this.state.phase))) return;
    this.reading = true;
    const attempt = ++this.generation;
    try {
      const saved = parseFormDraft(await this.request('GET', undefined, this.abort.signal), this.binding.componentId);
      if (attempt !== this.generation || this.abort.signal.aborted) return;
      this.publish({ phase: 'ready', saved, values: { ...saved.values } });
    } catch (problem) { if (attempt === this.generation) this.failed(problem); }
    finally { this.reading = false; }
  }
  change(field: keyof FormValues, value: string): void {
    if (!['ready', 'saving'].includes(this.state.phase) || !text(value) || !['name', 'entity_name'].includes(field)) return;
    this.generation++;
    this.queued[field] = value;
    this.publish({ ...this.state, phase: 'saving', values: { ...this.state.values, [field]: value } });
    this.startWrite();
  }
  private startWrite() {
    if (this.writing || !this.state.saved || this.state.phase !== 'saving') return;
    this.writing = this.write().finally(() => { this.writing = null; });
  }
  private async write() {
    while (!this.abort.signal.aborted && this.state.phase === 'saving' && Object.keys(this.queued).length && this.state.saved) {
      const previous = this.state.saved, values = this.queued; this.queued = {};
      try {
        const saved = parseFormDraft(await this.request('POST', { id: previous.id, expected_revision: previous.revision, values }, this.abort.signal), this.binding.componentId);
        if (this.abort.signal.aborted) return;
        if (saved.id !== previous.id || saved.revision < previous.revision) return unavailable();
        this.publish({ phase: Object.keys(this.queued).length ? 'saving' : 'ready', saved,
          values: { ...saved.values, ...this.queued } });
      } catch (problem) { this.queued = {}; this.failed(problem); return; }
    }
  }
  async flush(): Promise<boolean> {
    if (this.writing) await this.writing;
    return this.state.phase === 'ready' && !!this.state.saved;
  }
  async clear(submitted: FormDraft): Promise<boolean> {
    if (this.state.phase !== 'ready' || !this.state.saved || this.state.saved.id !== submitted.id
      || this.state.saved.revision !== submitted.revision || this.writing || Object.keys(this.queued).length) return false;
    this.queued = blank();
    this.publish({ ...this.state, phase: 'saving', values: blank() }); this.startWrite();
    return this.flush();
  }
  dispose() {
    if (this.state.phase === 'closed') return;
    this.generation++; this.abort.abort(); this.queued = {};
    this.publish({ phase: 'closed', saved: null, values: blank() });
  }
}

export interface NativeFormActivity { id: string; ui_id: string; form_id: string; component_id: string; revision: number; action: 'form_set_value' | 'form_set_values'; at_ms: number }
export function parseFormActivity(value: unknown, ui: string): NativeFormActivity {
  if (!object(value) || !exact(value, ['id', 'ui_id', 'form_id', 'component_id', 'revision', 'action', 'at_ms'])
    || !uuid(value.id) || !uuid(value.ui_id) || value.ui_id !== ui || !uuid(value.form_id) || !handle(value.component_id)
    || !counter(value.revision) || !counter(value.at_ms) || !['form_set_value', 'form_set_values'].includes(String(value.action))) return unavailable();
  return { id: value.id, ui_id: value.ui_id, form_id: value.form_id, component_id: value.component_id,
    revision: value.revision, action: value.action as NativeFormActivity['action'], at_ms: value.at_ms };
}
export class FormActivityCursor {
  private seen = new Set<string>();
  accept(activity: Pick<NativeFormActivity,'id'|'ui_id'> | undefined, ui: string | null, initial = false): boolean {
    if (!activity || !ui || activity.ui_id !== ui || this.seen.has(activity.id)) return false;
    this.seen.add(activity.id);
    if (this.seen.size > 128) this.seen.delete(this.seen.values().next().value!);
    return !initial;
  }
}
