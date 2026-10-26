import { csrfToken } from './api.js';

export type ThemeChoice = 'system' | 'light' | 'dark';
export type BuiltinView = 'client-facts' | 'workspace-guide';
export type PersonalChoices = { theme: ThemeChoice; active_view: BuiltinView };
export type PreferencesSnapshot = PersonalChoices & { preferences_id: string; user_id: string; revision: number };
type Result = { state: 'ready'; value: PreferencesSnapshot } | { state: 'conflict' | 'unavailable' };
type Fetcher = typeof fetch;
const unavailable = (): Result => ({ state: 'unavailable' });
const options = (): RequestInit => ({ credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(10_000) });
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const validChoices = (value: PersonalChoices): boolean => ['system', 'light', 'dark'].includes(value.theme)
  && ['client-facts', 'workspace-guide'].includes(value.active_view);

async function parse(response: Response, userId: string): Promise<PreferencesSnapshot> {
  if (!response.ok || response.headers.get('Content-Type')?.split(';')[0].trim() !== 'application/json') throw Error('Preferences unavailable');
  const reader = response.body?.getReader();
  if (!reader) throw Error('Preferences unavailable');
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 4096) throw Error('Preferences unavailable');
      chunks.push(value);
    }
  } finally { await reader.cancel(); }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  const value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
  if (!value || Array.isArray(value) || Object.keys(value).sort().join(',') !== 'active_view,preferences_id,revision,theme,user_id'
    || value.user_id !== userId || typeof value.preferences_id !== 'string' || !uuid.test(value.preferences_id)
    || !Number.isInteger(value.revision) || value.revision < 0 || value.revision >= 2_000_000_000
    || !validChoices(value)) throw Error('Preferences unavailable');
  return value as PreferencesSnapshot;
}

export async function readPreferences(userId: string, fetcher: Fetcher = fetch): Promise<Result> {
  try { return { state: 'ready', value: await parse(await fetcher('/api/workspace/preferences/', options()), userId) }; }
  catch { return unavailable(); }
}

export async function savePreferences(previous: PreferencesSnapshot, choice: PersonalChoices, fetcher: Fetcher = fetch): Promise<Result> {
  try {
    if (Object.keys(choice).sort().join(',') !== 'active_view,theme' || !validChoices(choice)) return unavailable();
    const token = await csrfToken(fetcher);
    const response = await fetcher('/api/workspace/preferences/', { ...options(), method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token, 'X-Maipa-Preferences': previous.preferences_id },
      body: JSON.stringify({ expected_revision: previous.revision, theme: choice.theme, active_view: choice.active_view }) });
    if (response.status === 409) return { state: 'conflict' };
    const value = await parse(response, previous.user_id);
    if (value.preferences_id !== previous.preferences_id || value.revision !== previous.revision + 1
      || value.theme !== choice.theme || value.active_view !== choice.active_view) return unavailable();
    return { state: 'ready', value };
  } catch { return unavailable(); }
}

export type PreferencesState = PersonalChoices & { phase: 'loading' | 'ready' | 'saving' | 'conflict' | 'unavailable' };
export const initialPreferences = (): PreferencesState => ({ phase: 'loading', theme: 'system', active_view: 'client-facts' });

/** One principal per mount; accepted revisions alone advance the save baseline. */
export class PreferencesController {
  current: PreferencesState = initialPreferences();
  private saved: PreferencesSnapshot | null = null;
  private generation = 0;
  private disposed = false;
  constructor(private userId: string, private publish: (state: PreferencesState) => void, private fetcher: Fetcher = fetch) {}
  private update(state: PreferencesState) { this.current = state; if (!this.disposed) this.publish(state); }
  async load() {
    if (this.disposed) return;
    const generation = ++this.generation;
    this.saved = null;
    this.update({ ...this.current, phase: 'loading' });
    const result = await readPreferences(this.userId, this.fetcher);
    if (this.disposed || generation !== this.generation) return;
    if (result.state !== 'ready') { this.update({ ...this.current, phase: 'unavailable' }); return; }
    this.saved = result.value;
    this.update({ phase: 'ready', theme: result.value.theme, active_view: result.value.active_view });
  }
  async save(changes: Partial<PersonalChoices>) {
    if (this.disposed || this.current.phase !== 'ready' || !this.saved) return;
    const keys = Object.keys(changes);
    if (!keys.length || keys.some(key => !['theme', 'active_view'].includes(key))) return;
    const choice = { theme: this.current.theme, active_view: this.current.active_view, ...changes };
    if (!validChoices(choice) || (choice.theme === this.saved.theme && choice.active_view === this.saved.active_view)) return;
    const generation = ++this.generation;
    this.update({ phase: 'saving', ...choice });
    const result = await savePreferences(this.saved, choice, this.fetcher);
    if (this.disposed || generation !== this.generation) return;
    if (result.state === 'ready') {
      this.saved = result.value;
      this.update({ phase: 'ready', theme: result.value.theme, active_view: result.value.active_view });
    } else this.update({ ...this.current, phase: result.state });
  }
  dispose() { this.disposed = true; ++this.generation; }
}
