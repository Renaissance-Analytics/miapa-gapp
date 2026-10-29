import assert from 'node:assert/strict';
import test from 'node:test';
import { parseChatState, readEventStream, reconnectChat, newerChat } from '../.test-build/agent-chat.js';

const state = { id: null, revision: 0, phase: 'offline', messages: [], ui: null,
  connection: { revision: 0, account_id: 'owned', provider: 'independent-acp', label: 'Independent ACP', available: true },
  providers: [{ id: 'independent-acp', label: 'Independent ACP', available: true }] };

test('UI acknowledgments stay monotonic within the same lease while revocation and replacement still win',()=>{
  const ui={account_id:'owned',id:'lease',revision:9,composition:{title:'New',schema:{id:'note',type:'Maipa.Note',props:{text:'Confirmed'}}}};
  const previous={...state,revision:10,ui};
  assert.equal(newerChat(previous,{...previous,revision:11,ui:{...ui,revision:8}}).ui.revision,9);
  assert.equal(newerChat(previous,{...previous,ui:null}).ui,null);
  assert.equal(newerChat(previous,{...previous,ui:{...ui,id:'fresh',revision:0}}).ui.id,'fresh');
  const switched={...state,connection:{...state.connection,revision:1,account_id:'next'}};
  assert.equal(newerChat(previous,switched),switched);
});

test('model choices come from a closed provider catalog and require a known current model', () => {
  const models = { currentModelId: 'provider-a', availableModels: [
    { modelId: 'provider-a', name: 'Provider model A' }, { modelId: 'provider-b', name: 'Provider model B' },
  ] };
  assert.deepEqual(parseChatState({ ...state, phase: 'ready', models }).models, models);
  for (const invalid of [{ ...models, currentModelId: 'foreign' },
    { ...models, availableModels: [...models.availableModels, models.availableModels[0]] },
    { ...models, availableModels: [{ modelId: 'provider-a', name: 'Private\nvalue' }] },
    { ...models, availableModels: [{ modelId: 'provider-a', name: 'A', command: 'untrusted' }] },
    { ...models, account_id: 'foreign' }]) assert.throws(() => parseChatState({ ...state, phase: 'ready', models: invalid }));
  assert.throws(() => parseChatState({ ...state, models }));
});
test('reconnect reads current owned state starts only a stopped worker and preserves conflicts without retry', async () => {
  const saved = { ...state, id: 'owned-conversation', revision: 19, phase: 'unavailable', messages: [
    { id: 'queued', role: 'user', text: 'Keep my saved request', state: 'queued' },
  ] };
  const original = globalThis.fetch;
  const token = 'c'.repeat(64);
  try {
    for (const phase of ['unavailable', 'offline', 'ready', 'thinking', 'connecting']) {
      const requests = [];
      globalThis.fetch = async (path, options = {}) => {
        requests.push([path, options]);
        if (path === '/api/session/csrf/') return Response.json({ csrf_token: token });
        assert.equal(options.headers['X-Maipa-Agent-Actor'], '7');
        assert.equal(options.credentials, 'same-origin');
        if (options.method === 'GET') return Response.json({ ...saved, phase });
        assert.deepEqual(JSON.parse(options.body), { action: 'connect', revision: 19, selection_revision: 0 });
        assert.equal(options.headers['X-CSRFToken'], token);
        return Response.json({ ...saved, phase: 'connecting', revision: 20 });
      };
      const result = await reconnectChat('7');
      assert.deepEqual(result.messages, saved.messages);
      assert.equal(requests.filter(([,options]) => options.method === 'POST').length,
        ['unavailable', 'offline'].includes(phase) ? 1 : 0);
    }
    let writes = 0;
    globalThis.fetch = async (path, options = {}) => {
      if (path === '/api/session/csrf/') return Response.json({ csrf_token: token });
      if (options.method === 'GET') return Response.json(saved);
      writes++; return new Response('', { status: 409 });
    };
    await assert.rejects(() => reconnectChat('7'), /Conversation changed/);
    assert.equal(writes, 1);
  } finally { globalThis.fetch = original; }
});
test('provider-neutral state orders switches before per-account chat revisions and rejects malformed choices', () => {
  const old = { ...state, revision: 200 };
  const next = { ...state, revision: 0, connection: { ...state.connection, revision: 1, account_id: 'next' } };
  assert.equal(newerChat(old, next), next);
  assert.equal(newerChat(next, old), next);
  assert.equal(parseChatState(next).connection.provider, 'independent-acp');
  for (const bad of [{ ...state, connection: { ...state.connection, revision: true } },
    { ...state, connection: { ...state.connection, provider: '../command' } },
    { ...state, providers: [...state.providers, ...state.providers] },
    { ...state, connection: { ...state.connection, command: 'untrusted' } }]) assert.throws(() => parseChatState(bad));
});
test('selected-provider sign-in accepts bounded HTTPS challenges without a vendor-specific URL or code format', () => {
  const login = { url: 'https://identity.example.test/authorize?session=synthetic', code: '123 456' };
  assert.deepEqual(parseChatState({ ...state, phase: 'sign_in', login }).login, login);
  assert.deepEqual(parseChatState({ ...state, phase: 'sign_in', login: { url: login.url } }).login, { url: login.url });
  for (const bad of [{ url: 'javascript:alert(1)' }, { url: 'http://identity.example.test' },
    { url: 'https://user:password@identity.example.test' }, { ...login, code: '\nsecret' },
    { ...login, code: 'x'.repeat(129) }]) assert.throws(() => parseChatState({ ...state, phase: 'sign_in', login: bad }));
  assert.throws(() => parseChatState({ ...state, phase: 'ready', login }));
  const privateLogin = { url: login.url, accepts_code: true };
  assert.deepEqual(parseChatState({ ...state, phase: 'sign_in', login: privateLogin }).login, privateLogin);
  for (const accepts_code of ['true', null, 1]) assert.throws(() => parseChatState({ ...state, phase: 'sign_in', login: { ...privateLogin, accepts_code } }));
});
test('an unsupported selected provider is retained without silently starting a different provider', async () => {
  const original = globalThis.fetch; const calls = [];
  const unavailable = { ...state, connection: { ...state.connection, available: false } };
  try {
    globalThis.fetch = async (path, options) => { calls.push(options.method); return Response.json(unavailable); };
    assert.deepEqual(await reconnectChat('7'), unavailable);
    assert.deepEqual(calls, ['GET']);
  } finally { globalThis.fetch = original; }
});
test('an empty conversation has no predefined panel and never claims a provider connection', () => {
  assert.deepEqual(parseChatState(state), state);
  for (const bad of [{ ...state, phase: 'invented' }, { ...state, revision: -1 },
    { ...state, messages: [{ id: 'x', role: 'system', text: 'secret', state: 'complete' }] }])
    assert.throws(() => parseChatState(bad));
});
test('stream parsing survives split UTF-8 and frames and stops after revocation', async () => {
  const frames = 'event: state\ndata: ' + JSON.stringify({ ...state, phase: 'ready', messages: [
    { id: 'message', role: 'assistant', text: '€ Ω 🙂', state: 'complete' },
  ] }) + '\n\n'
    + ': heartbeat\n\nevent: revoked\ndata: {}\n\n';
  const wire = new TextEncoder().encode(frames);
  const stream = new ReadableStream({ start(controller) {
    for (const byte of wire) controller.enqueue(new Uint8Array([byte]));
    controller.close();
  } });
  const seen = [];
  await assert.rejects(() => readEventStream(stream, value => seen.push(value)), /Session changed/);
  assert.equal(seen.length, 1);
  assert.equal(seen[0].phase, 'ready');
  assert.equal(seen[0].messages[0].text, '€ Ω 🙂');
});
test('server composition preserves authored handles and rejects executable props', () => {
  const ui = { account_id: 'owned', id: 'u', revision: 1, composition: { title: 'Review',
    schema: { id: 'note', type: 'Maipa.Note', props: { text: 'Check evidence' } } } };
  assert.equal(parseChatState({ ...state, ui }).ui.composition.schema.id, 'note');
  assert.throws(() => parseChatState({ ...state, ui: { ...ui, account_id: 'foreign' } }));
  assert.throws(() => parseChatState({ ...state, ui: { ...ui, composition: { ...ui.composition,
    schema: { id: 'x', type: 'iframe', props: { src: 'https://foreign.invalid' } } } } }));
});
