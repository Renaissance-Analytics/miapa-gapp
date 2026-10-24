import { csrfToken } from './api.js';

export interface LayoutSnapshot {
  layout_id: string;
  user_id: string;
  revision: number;
  split_basis_points: number;
}
type Result = { state: 'ready'; value: LayoutSnapshot } | { state: 'conflict' | 'unavailable' };
type Fetcher = typeof fetch;
const unavailable = (): Result => ({ state: 'unavailable' });
const options = (): RequestInit => ({ credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(10_000) });
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

async function parse(response: Response, userId: string): Promise<LayoutSnapshot> {
  if (!response.ok || response.headers.get('Content-Type')?.split(';')[0].trim() !== 'application/json') throw Error('Layout unavailable');
  const reader = response.body?.getReader();
  if (!reader) throw Error('Layout unavailable');
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 4096) throw Error('Layout unavailable');
      chunks.push(value);
    }
  } finally { await reader.cancel(); }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  const value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
  if (!value || Array.isArray(value) || Object.keys(value).sort().join(',') !== 'layout_id,revision,split_basis_points,user_id'
    || value.user_id !== userId || typeof value.layout_id !== 'string' || !uuid.test(value.layout_id)
    || !Number.isInteger(value.revision) || value.revision < 0 || value.revision >= 2_000_000_000
    || !Number.isInteger(value.split_basis_points) || value.split_basis_points < 2200 || value.split_basis_points > 4500) throw Error('Layout unavailable');
  return value as LayoutSnapshot;
}

export async function readLayout(userId: string, fetcher: Fetcher = fetch): Promise<Result> {
  try { return { state: 'ready', value: await parse(await fetcher('/api/workspace/layout/', options()), userId) }; }
  catch { return unavailable(); }
}

export async function saveLayout(previous: LayoutSnapshot, points: number, fetcher: Fetcher = fetch): Promise<Result> {
  try {
    if (!Number.isInteger(points) || points < 2200 || points > 4500) return unavailable();
    const token = await csrfToken(fetcher);
    const response = await fetcher('/api/workspace/layout/', { ...options(), method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token, 'X-Maipa-Layout': previous.layout_id },
      body: JSON.stringify({ expected_revision: previous.revision, split_basis_points: points }) });
    if (response.status === 409) return { state: 'conflict' };
    const value = await parse(response, previous.user_id);
    if (value.layout_id !== previous.layout_id || value.revision !== previous.revision + 1 || value.split_basis_points !== points) return unavailable();
    return { state: 'ready', value };
  } catch { return unavailable(); }
}

export interface LayoutState { phase: 'loading' | 'ready' | 'saving' | 'conflict' | 'unavailable'; split: number }

/** One mounted principal owns one controller; never automatically retry a write. */
export class LayoutController {
  current: LayoutState = { phase: 'loading', split: 28 };
  private saved: LayoutSnapshot | null = null;
  private generation = 0;
  private disposed = false;
  constructor(private userId: string, private publish: (state: LayoutState) => void, private fetcher: Fetcher = fetch) {}
  private update(state: LayoutState) { this.current = state; if (!this.disposed) this.publish(state); }
  async load() {
    if (this.disposed) return;
    const generation = ++this.generation;
    this.saved = null;
    this.update({ ...this.current, phase: 'loading' });
    const result = await readLayout(this.userId, this.fetcher);
    if (this.disposed || generation !== this.generation) return;
    if (result.state !== 'ready') { this.update({ ...this.current, phase: 'unavailable' }); return; }
    this.saved = result.value;
    this.update({ phase: 'ready', split: result.value.split_basis_points / 100 });
  }
  change(split: number) {
    if (!this.disposed && this.current.phase === 'ready' && Number.isFinite(split) && split >= 22 && split <= 45)
      this.update({ phase: 'ready', split: Math.round(split * 100) / 100 });
  }
  async commit(split: number) {
    if (this.disposed || this.current.phase !== 'ready' || !this.saved || !Number.isFinite(split) || split < 22 || split > 45) return;
    this.change(split);
    const points = Math.round(split * 100);
    if (points === this.saved.split_basis_points) return;
    const generation = ++this.generation;
    this.update({ ...this.current, phase: 'saving' });
    const result = await saveLayout(this.saved, points, this.fetcher);
    if (this.disposed || generation !== this.generation) return;
    if (result.state === 'ready') {
      this.saved = result.value;
      this.update({ phase: 'ready', split: result.value.split_basis_points / 100 });
    } else this.update({ ...this.current, phase: result.state });
  }
  dispose() { this.disposed = true; ++this.generation; }
}
