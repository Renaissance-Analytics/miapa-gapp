import { MicroMcpServer, type JsonObject } from '@particle-academy/agent-integrations/mcp';
import { registerScreensBridge } from '@particle-academy/agent-integrations/bridges/screens';
import type { ScreenSchema } from '@particle-academy/fancy-screens';
import { compositionSchema, type Composition } from './agent-chat.js';
import { csrfToken } from './api.js';

export interface ViewDefinition { title: string; revision: number; schema: ScreenSchema | null; available: boolean }
export interface PersonalView extends ViewDefinition { id: string; pinned: boolean; history: ViewDefinition[] }
export interface ViewsSnapshot { views_id: string; user_id: string; revision: number; views: PersonalView[] }
export interface ViewsState { phase: 'loading' | 'ready' | 'saving' | 'conflict' | 'unavailable'; value: ViewsSnapshot | null; activeId: string | null }
const object = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const exact = (v: Record<string, unknown>, keys: string[]) => Object.keys(v).sort().join(',') === keys.sort().join(',');
const uuid = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(v);
const revision = (v: unknown): v is number => Number.isSafeInteger(v) && Number(v) >= 0 && Number(v) < 2_000_000_000;
const title = (v: unknown): v is string => typeof v === 'string' && !!v.trim() && v.length <= 80 && !/[\u0000-\u001f\u007f]/.test(v);
const unavailable = () => Error('Saved views could not be confirmed. Reload saved views.');
export class ViewsConflict extends Error { constructor() { super('Saved views changed. Reload saved views before editing.'); } }

function confirmedUi(input: unknown, previous: Composition, expected: Pick<PersonalView,'title'|'schema'>) {
  if (!object(input) || !exact(input,['id','revision','composition']) || input.id !== previous.id || input.revision !== previous.revision+1
    || !object(input.composition) || !exact(input.composition,['title','schema']) || input.composition.title !== expected.title
    || JSON.stringify(compositionSchema(input.composition.schema)) !== JSON.stringify(expected.schema)) throw unavailable();
  return { ...previous, revision: Number(input.revision), composition: { title: expected.title, schema: expected.schema! } };
}

export function parseViews(input: unknown, userId: string): ViewsSnapshot {
  const definition = (v: unknown): ViewDefinition => {
    if (!object(v) || !title(v.title) || !revision(v.revision) || v.revision < 1 || typeof v.available !== 'boolean') throw unavailable();
    if (v.available ? v.schema === null : v.schema !== null) throw unavailable();
    return { title: v.title, revision: v.revision, available: v.available,
      schema: v.schema === null ? null : compositionSchema(v.schema) };
  };
  if (!object(input) || !exact(input, ['views_id', 'user_id', 'revision', 'views']) || !uuid(input.views_id)
    || input.user_id !== userId || !revision(input.revision) || !Array.isArray(input.views) || input.views.length > 8) throw unavailable();
  const ids = new Set<string>();
  const views = input.views.map((v: unknown): PersonalView => {
    if (!object(v) || !exact(v, ['id','title','schema','revision','pinned','history','available']) || !uuid(v.id) || ids.has(v.id)
      || typeof v.pinned !== 'boolean' || !Array.isArray(v.history) || v.history.length > 4) throw unavailable();
    ids.add(v.id);const current = definition(v);
    let previous = 0;
    const history = v.history.map((entry: unknown) => {
      if (!object(entry) || !exact(entry, ['title','schema','revision','available'])) throw unavailable();
      const parsed = definition(entry);
      if (parsed.revision <= previous || parsed.revision >= current.revision) throw unavailable();
      previous = parsed.revision;return parsed;
    });
    return { id: v.id, ...current, pinned: v.pinned, history };
  });
  return { views_id: input.views_id, user_id: userId, revision: input.revision, views };
}

async function request(userId: string, value?: Record<string, unknown>, state?: ViewsSnapshot, signal?: AbortSignal): Promise<ViewsSnapshot> {
  const headers: Record<string,string> = {};
  if (value) { headers['Content-Type'] = 'application/json';headers['X-Maipa-Views'] = state!.views_id;headers['X-CSRFToken'] = await csrfToken(fetch); }
  if (signal?.aborted) throw unavailable();
  const response = await fetch('/api/workspace/views/', { method: value ? 'POST' : 'GET', credentials: 'same-origin',
    cache: 'no-store', redirect: 'error', signal: signal ?? AbortSignal.timeout(15000), headers,
    ...(value ? { body: JSON.stringify({ ...value, expected_revision: state!.revision }) } : {}) });
  if (response.status === 409) throw new ViewsConflict();
  if (response.status !== 200) throw unavailable();
  return parseViews(await response.json(), userId);
}

export async function viewUiRequest(userId: string, ui: Composition, name: string, args: Record<string,unknown>, signal: AbortSignal) {
  const token = await csrfToken(fetch);
  if (signal.aborted) throw unavailable();
  const response = await fetch(`/api/agent/accounts/${encodeURIComponent(ui.account_id)}/ui/${encodeURIComponent(ui.id)}/`, {
    method:'POST',credentials:'same-origin',cache:'no-store',redirect:'error',signal,
    headers:{'Content-Type':'application/json','X-CSRFToken':token,'X-Maipa-Agent-Actor':userId},
    body:JSON.stringify({method:'tools/call',params:{name,arguments:{...args,revision:ui.revision}}}) });
  if (response.status === 409) throw new ViewsConflict();
  if (response.status !== 200) throw unavailable();
  return response.json() as Promise<unknown>;
}

/** Public awaited Screens adapter over server-owned definitions, scoped to one mounted user. */
export class PersonalViewsController {
  readonly server = new MicroMcpServer({info:{name:'maipa-personal-views',version:'0.1.0'}});
  state: ViewsState = {phase:'loading',value:null,activeId:null};
  private closed = false;
  private sequence = 0;
  private pending = false;
  private lifetime = new AbortController();
  private bridge;
  constructor(private userId: string, private changed: (state: ViewsState) => void,
    private currentUi: () => Composition | null,
    private opened: (view: PersonalView) => void,
    private committed: (ui: Composition) => void = () => {}) {
    if (!/^[0-9]+$/.test(userId)) throw unavailable();
    this.bridge = registerScreensBridge(this.server, {agent:{id:'maipa',name:'Maipa'},adapter:{
      listScreens: () => (this.state.value?.views ?? []).filter(v => v.pinned && v.available).map(v => ({id:v.id,title:v.title,active:v.id===this.state.activeId,kind:'maipa-components'})),
      getActive: () => this.state.activeId,
      listKinds: () => [{kind:'maipa-components',label:'Registered Maipa components',description:'Server-validated component definitions; no executable code or saved native authority.'}],
      setActive: async id => { await this.open(id); },
      createScreen: async spec => {
        if (!uuid(spec.id) || spec.kind !== 'maipa-components' || !title(spec.title) || !object(spec.config) || !exact(spec.config,['schema'])) throw unavailable();
        await this.mutate({action:'create',id:spec.id,title:spec.title,schema:compositionSchema(spec.config.schema)});
      },
      destroyScreen: async id => { await this.mutate({action:'unpin',id}); },
      updateScreenContent: async (id, partial) => {
        const existing = this.own(id);
        if (exact(partial,['pinned']) && typeof partial.pinned === 'boolean') {
          await this.mutate({action:partial.pinned?'pin':'unpin',id});return;
        }
        if (Object.keys(partial).length === 0 || Object.keys(partial).some(key => !['title','schema'].includes(key))) throw unavailable();
        const name = partial.title ?? existing.title;if (!title(name)) throw unavailable();
        await this.mutate({action:'schema' in partial?'update':'rename',id,title:name,
          ...('schema' in partial?{schema:compositionSchema(partial.schema)}:{})});
      },
    }});
  }
  private notify(next: ViewsState) { if (!this.closed) { this.state=next;this.changed(next); } }
  private own(id: string) {
    const view = this.state.value?.views.find(v => v.id === id && v.available);
    if (!view || !uuid(id)) throw unavailable();return view;
  }
  async load(): Promise<void> {
    if (this.closed || this.pending) return;
    const sequence = ++this.sequence;
    try {
      const value = await request(this.userId,undefined,undefined,AbortSignal.any([this.lifetime.signal,AbortSignal.timeout(15000)]));
      if (this.closed || sequence !== this.sequence) return;
      const activeId = value.views.some(v => v.id===this.state.activeId && v.available) ? this.state.activeId : null;
      this.notify({phase:'ready',value,activeId});
    } catch { if (!this.closed && sequence === this.sequence) this.notify({phase:'unavailable',value:null,activeId:null}); }
  }
  private async mutate(value: Record<string,unknown>) {
    if (this.closed || this.pending || this.state.phase !== 'ready' || !this.state.value) throw unavailable();
    const previous = this.state;this.pending=true;++this.sequence;this.notify({...previous,phase:'saving'});
    try {
      const ui = this.currentUi();
      let saved: ViewsSnapshot;
      const signal = AbortSignal.any([this.lifetime.signal,AbortSignal.timeout(15000)]);
      if (ui && value.id === previous.activeId && ['rename','update'].includes(String(value.action))) {
        const response = await viewUiRequest(this.userId,ui,'maipa_views_change',{
          views_id:previous.value!.views_id,expected_revision:previous.value!.revision,...value},signal);
        if (!object(response) || !exact(response,['views','ui'])) throw unavailable();
        saved=parseViews(response.views,this.userId);
        const view=saved.views.find(v => v.id===value.id);if (!view) throw unavailable();
        const accepted=confirmedUi(response.ui,ui,view);if (!this.closed) this.committed(accepted);
      } else saved=await request(this.userId,value,previous.value!,signal);
      if (this.closed) throw unavailable();
      this.notify({phase:'ready',value:saved,activeId:previous.activeId});
    } catch (error) {
      if (!this.closed) this.notify({...previous,phase:error instanceof ViewsConflict?'conflict':'unavailable'});
      throw error instanceof ViewsConflict ? error : unavailable();
    } finally { this.pending=false; }
  }
  private async open(id: string) {
    await this.load();
    if (this.closed || this.state.phase !== 'ready' || !this.state.value) throw unavailable();
    const view = this.own(id), value = this.state.value, ui = this.currentUi();
    this.pending=true;this.notify({...this.state,phase:'saving'});
    try {
      if (ui) {
        const accepted=await viewUiRequest(this.userId,ui,'maipa_views_open',{
          views_id:value.views_id,expected_revision:value.revision,id},AbortSignal.any([this.lifetime.signal,AbortSignal.timeout(15000)]));
        const confirmed=confirmedUi(accepted,ui,view);if (!this.closed) this.committed(confirmed);
      }
      if (this.closed) throw unavailable();
      this.notify({...this.state,phase:'ready',activeId:id});this.opened(view);
    } catch(error) {
      if(!this.closed)this.notify({...this.state,phase:error instanceof ViewsConflict?'conflict':'unavailable'});
      throw error;
    } finally {this.pending=false;}
  }
  async tool(name: string, args: Record<string,unknown>) {
    if (this.closed) throw unavailable();
    const result=await this.server.callTool(name,JSON.parse(JSON.stringify(args)) as JsonObject);
    if (result.isError) {
      if (object(result.structuredContent) && result.structuredContent.created === true) throw Error('The view was saved. Open its pin to show it.');
      throw this.state.phase === 'conflict' ? new ViewsConflict() : unavailable();
    }
  }
  async save(composition: NonNullable<Composition['composition']>) {
    await this.tool('screens_create',{id:crypto.randomUUID(),kind:'maipa-components',title:composition.title,config:{schema:composition.schema}});
  }
  async reorder(ids: string[]) { await this.mutate({action:'reorder',ids}); }
  clearActive() { this.notify({...this.state,activeId:null}); }
  dispose() { this.closed=true;++this.sequence;this.lifetime.abort();this.bridge.dispose(); }
}
