import assert from 'node:assert/strict';
import test from 'node:test';
import { parseChatState, readEventStream, reconnectChat } from '../.test-build/agent-chat.js';

const state = { id: null, revision: 0, phase: 'offline', messages: [], ui: null };
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
        assert.deepEqual(JSON.parse(options.body), { action: 'connect', revision: 19 });
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
  const ui = { account_id: 'a', id: 'u', revision: 1, composition: { title: 'Review',
    schema: { id: 'note', type: 'Maipa.Note', props: { text: 'Check evidence' } } } };
  assert.equal(parseChatState({ ...state, ui }).ui.composition.schema.id, 'note');
  assert.throws(() => parseChatState({ ...state, ui: { ...ui, composition: { ...ui.composition,
    schema: { id: 'x', type: 'iframe', props: { src: 'https://foreign.invalid' } } } } }));
});
