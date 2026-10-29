import type { ScreenSchema } from '@particle-academy/fancy-screens';
import { COOLDOWN_MESSAGE, parseTurnFailure, type TurnFailure } from './turn-failure.js';
import { csrfToken } from './api.js';

export type ChatPhase = 'offline' | 'connecting' | 'sign_in' | 'ready' | 'thinking' | 'stopping' | 'unavailable';
export interface ChatMessage { id: string; role: 'user' | 'assistant'; text: string; state: 'queued' | 'streaming' | 'complete' | 'interrupted' }
export interface Composition { account_id: string; id: string; revision: number; composition: { title: string; schema: ScreenSchema } | null }
export interface ProviderChoice { id: string; label: string; available: boolean }
export interface ChatConnection { revision: number; account_id: string | null; provider: string | null; label: string | null; available: boolean }
export interface ModelState { currentModelId: string | null; availableModels: { modelId: string; name: string }[] }
export interface ChatState { id: string | null; revision: number; phase: ChatPhase; messages: ChatMessage[]; ui: Composition | null; failure?: TurnFailure | null;
  connection: ChatConnection; providers: ProviderChoice[]; login?: { url: string; code?: string; accepts_code?: boolean }; models?: ModelState; }
export const emptyChat = (): ChatState => ({ id: null, revision: 0, phase: 'offline', messages: [], ui: null,
  connection: { revision: 0, account_id: null, provider: null, label: null, available: false }, providers: [] });
const object = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const fail = (): never => { throw Error('Conversation unavailable'); };
const closed = (v: Record<string, unknown>, fields: string[]) => Object.keys(v).every(k => fields.includes(k));
const identifier = (v: unknown): v is string => typeof v === 'string' && v.length > 0 && v.length <= 200;
const providerId = (v: unknown): v is string => typeof v === 'string' && v === v.trim() && /^[a-z][a-z0-9-]{0,63}$/.test(v);
const label = (v: unknown): v is string => typeof v === 'string' && !!v.trim() && v.length <= 80 && !/[\u0000-\u001f\u007f]/.test(v);

/** Conversation revisions are local to an account; selection epochs order switches. */
export function newerChat(previous: ChatState, next: ChatState): ChatState {
  if (next.connection.revision > previous.connection.revision) return next;
  if (next.connection.revision !== previous.connection.revision || next.revision < previous.revision) return previous;
  // A UI POST can acknowledge ahead of an already in-flight SSE snapshot.
  // Only the same live lease is monotonic; null/replacement authority still wins.
  if (next.ui && previous.ui && next.ui.id === previous.ui.id && next.ui.account_id === previous.ui.account_id
    && next.ui.revision < previous.ui.revision) return { ...next, ui: previous.ui };
  return next;
}

export function compositionSchema(input: unknown): ScreenSchema {
  const ids = new Set<string>(), controls = new Set<string>();
  let count = 0;
  function node(v: unknown, depth: number): ScreenSchema {
    if (!object(v) || !closed(v, ['id', 'type', 'props', 'children']) || typeof v.id !== 'string'
      || !/^[a-z][a-z0-9-]{0,47}$/.test(v.id) || ids.has(v.id) || ++count > 16 || depth > 4) return fail();
    ids.add(v.id);
    if (v.type === 'Maipa.Stack') {
      if (v.props !== undefined || !Array.isArray(v.children) || !v.children.length || v.children.length > 16) return fail();
      return { id: v.id, type: v.type, children: v.children.map(child => node(child, depth + 1)) };
    }
    if (v.children !== undefined) return fail();
    if (['Maipa.Note', 'Maipa.Heading', 'Maipa.Callout'].includes(String(v.type))) {
      const callout = v.type === 'Maipa.Callout';
      if (!object(v.props) || !closed(v.props, callout ? ['text', 'tone'] : ['text']) || typeof v.props.text !== 'string' || !v.props.text.trim()
        || v.props.text.length > (v.type === 'Maipa.Heading' ? 120 : 2000) || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(v.props.text)
        || (callout && !['info', 'attention', 'neutral'].includes(String(v.props.tone)))) return fail();
      return { id: v.id, type: String(v.type), props: { text: v.props.text, ...(callout ? { tone: v.props.tone } : {}) } };
    }
    if (v.type === 'Maipa.ClientTable') {
      if (!object(v.props) || !closed(v.props, ['columns']) || !Array.isArray(v.props.columns)
        || !v.props.columns.length || v.props.columns.length > 2 || controls.has(v.type)
        || v.props.columns.some(column => typeof column !== 'string' || !['name', 'entity_count'].includes(column))
        || new Set(v.props.columns).size !== v.props.columns.length) return fail();
      controls.add(v.type);
      return { id: v.id, type: v.type, props: { columns: [...v.props.columns] } };
    }
    if (typeof v.type !== 'string' || !['Maipa.ClientFacts', 'Maipa.ClientCatalog', 'Maipa.AdminUsers'].includes(v.type)
      || v.props !== undefined || controls.has(v.type)) return fail();
    controls.add(v.type);
    return { id: v.id, type: v.type };
  }
  return node(input, 0);
}

export function parseChatState(v: unknown): ChatState {
  if (!object(v) || !closed(v, ['id', 'revision', 'phase', 'messages', 'ui', 'login', 'connection', 'providers', 'models', 'failure'])
    || (v.id !== null && !identifier(v.id)) || !Number.isSafeInteger(v.revision) || Number(v.revision) < 0
    || !['offline', 'connecting', 'sign_in', 'ready', 'thinking', 'stopping', 'unavailable'].includes(String(v.phase))
    || !Array.isArray(v.messages) || v.messages.length > 128) return fail();
  const c = v.connection;
  if (!object(c) || !closed(c, ['revision', 'account_id', 'provider', 'label', 'available'])
    || !Number.isSafeInteger(c.revision) || Number(c.revision) < 0 || typeof c.available !== 'boolean'
    || (c.account_id === null ? c.provider !== null || c.label !== null || c.available
      : !identifier(c.account_id) || !providerId(c.provider) || !label(c.label))
    || !Array.isArray(v.providers) || v.providers.length > 32) return fail();
  const connection = c as unknown as ChatConnection;
  const keys = new Set<string>();
  const providers = v.providers.map((p: unknown): ProviderChoice => {
    if (!object(p) || !closed(p, ['id', 'label', 'available']) || !providerId(p.id) || !label(p.label)
      || typeof p.available !== 'boolean' || keys.has(p.id)) return fail();
    keys.add(p.id); return { id: p.id, label: p.label, available: p.available };
  });
  let models: ModelState | undefined;
  if (v.models !== undefined) {
    const value = v.models;
    if (!connection.account_id || !['ready', 'thinking', 'connecting'].includes(String(v.phase))
      || !object(value) || !closed(value, ['currentModelId', 'availableModels'])
      || !Array.isArray(value.availableModels) || !value.availableModels.length || value.availableModels.length > 128) return fail();
    const ids = new Set<string>();
    const availableModels = value.availableModels.map(item => {
      if (!object(item) || !closed(item, ['modelId', 'name']) || !identifier(item.modelId)
        || item.modelId.trim() !== item.modelId || /[\u0000-\u001f\u007f]/.test(item.modelId)
        || typeof item.name !== 'string' || !item.name.trim() || item.name.length > 120 || /[\u0000-\u001f\u007f]/.test(item.name)
        || ids.has(item.modelId)) return fail();
      ids.add(item.modelId); return { modelId: item.modelId, name: item.name };
    });
    if (value.currentModelId !== null && (typeof value.currentModelId !== 'string' || !ids.has(value.currentModelId))) return fail();
    models = { currentModelId: value.currentModelId as string | null, availableModels };
  }
  const messages = v.messages.map((m: unknown): ChatMessage => {
    if (!object(m) || !closed(m, ['id', 'role', 'text', 'state']) || !identifier(m.id)
      || !['user', 'assistant'].includes(String(m.role)) || typeof m.text !== 'string' || m.text.length > 65536
      || !['queued', 'streaming', 'complete', 'interrupted'].includes(String(m.state))) return fail();
    return m as unknown as ChatMessage;
  });
  let ui: Composition | null = null;
  if (v.ui !== null) {
    const u = v.ui;
    if (!object(u) || !closed(u, ['account_id', 'id', 'revision', 'composition']) || !identifier(u.id)
      || !identifier(u.account_id) || u.account_id !== connection.account_id || !Number.isSafeInteger(u.revision) || Number(u.revision) < 0) return fail();
    let composition: Composition['composition'] = null;
    if (u.composition !== null) {
      if (!object(u.composition) || !closed(u.composition, ['title', 'schema']) || typeof u.composition.title !== 'string'
        || !u.composition.title.trim() || u.composition.title.length > 80) return fail();
      composition = { title: u.composition.title, schema: compositionSchema(u.composition.schema) };
    }
    ui = { id: u.id, account_id: u.account_id, revision: Number(u.revision), composition };
  }
  let login: ChatState['login'];
  if (v.login !== undefined) {
    if (!object(v.login) || !closed(v.login, ['url', 'code', 'accepts_code']) || typeof v.login.url !== 'string'
      || (v.login.accepts_code !== undefined && typeof v.login.accepts_code !== 'boolean')
      || v.login.url.length > 2048 || /[\u0000-\u0020\u007f]/.test(v.login.url)) return fail();
    const url = new URL(v.login.url);
    if (url.protocol !== 'https:' || !url.hostname || url.username || url.password || !connection.account_id
      || v.phase !== 'sign_in' || (v.login.code !== undefined && (typeof v.login.code !== 'string'
        || !v.login.code.trim() || v.login.code.length > 128 || /[\u0000-\u001f\u007f]/.test(v.login.code)))) return fail();
    login = { url: v.login.url, ...(v.login.code !== undefined ? { code: v.login.code as string } : {}),
      ...(v.login.accepts_code !== undefined ? { accepts_code: v.login.accepts_code as boolean } : {}) };
  }
  let failure: TurnFailure | null | undefined;
  if ('failure' in v) {
    failure = parseTurnFailure(v.failure);
    if (failure && (!connection.account_id || v.id === null)) return fail();
  }
  return { id: v.id as string | null, revision: Number(v.revision), phase: v.phase as ChatPhase, messages, ui,
    connection, providers, ...(login ? { login } : {}), ...(models ? { models } : {}), ...('failure' in v ? {failure} : {}) };
}

export async function readEventStream(body: ReadableStream<Uint8Array>, receive: (state: ChatState) => void) {
  const reader = body.getReader(), decoder = new TextDecoder('utf-8', { fatal: true });
  let buffer = '';
  try {
    while (true) {
      const next = await reader.read();
      if (next.done) { buffer += decoder.decode(); if (buffer.trim()) fail(); return; }
      buffer += decoder.decode(next.value, { stream: true });
      if (buffer.length > 1_048_576) fail();
      let end: number;
      while ((end = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, end); buffer = buffer.slice(end + 2);
        const lines = frame.split('\n');
        if (lines.includes('event: revoked')) throw Error('Session changed');
        if (lines.includes('event: state')) {
          const data = lines.filter(line => line.startsWith('data: ')).map(line => line.slice(6)).join('\n');
          receive(parseChatState(JSON.parse(data)));
        }
      }
    }
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}

export async function chatRequest(userId: string, payload?: Record<string, unknown>, signal?: AbortSignal): Promise<ChatState> {
  const headers: Record<string, string> = { 'X-Maipa-Agent-Actor': userId, 'X-Maipa-Agent-Models': '1', 'X-Maipa-Agent-Failures': '1' };
  if (payload) { headers['Content-Type'] = 'application/json'; headers['X-CSRFToken'] = await csrfToken(fetch); }
  const response = await fetch('/api/agent/chat/', { credentials: 'same-origin', cache: 'no-store', redirect: 'error',
    method: payload ? 'POST' : 'GET', headers, ...(payload ? { body: JSON.stringify(payload) } : {}),
    signal: signal ?? AbortSignal.timeout(15000) });
  if (response.status === 409) throw Error('Conversation changed. Reconnect to load the latest conversation.');
  if (response.status === 429) throw Error(COOLDOWN_MESSAGE);
  if (response.status !== 200) fail();
  return parseChatState(await response.json());
}

/** Reload current authority/revision before restarting only a stopped consumer. */
export async function reconnectChat(userId: string): Promise<ChatState> {
  const saved = await chatRequest(userId);
  return saved.connection.available && ['offline', 'unavailable'].includes(saved.phase)
    ? chatRequest(userId, { action: 'connect', revision: saved.revision, selection_revision: saved.connection.revision }) : saved;
}

export async function dismissComposition(userId: string, ui: Composition): Promise<void> {
  const token = await csrfToken(fetch);
  const response = await fetch(`/api/agent/accounts/${encodeURIComponent(ui.account_id)}/ui/${encodeURIComponent(ui.id)}/`, {
    method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(15000),
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token, 'X-Maipa-Agent-Actor': userId },
    body: JSON.stringify({ method: 'tools/call', params: { name: 'maipa_ui_dismiss', arguments: { revision: ui.revision } } }),
  });
  if (response.status !== 200) throw Error('The shared panel changed. Reconnect before closing it.');
}
