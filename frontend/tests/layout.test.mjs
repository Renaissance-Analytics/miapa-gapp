import assert from 'node:assert/strict';
import test from 'node:test';
import { LayoutController, readLayout, saveLayout } from '../.test-build/layout.js';

const snapshot = { layout_id: '61525eda-df14-4e4b-a369-4f8c20dc95ed', user_id: '1', revision: 0, split_basis_points: 2800 };
const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const csrf = () => json({ csrf_token: 'a'.repeat(64) });

test('saved layout reads are bounded same-origin and bound to the expected user', async () => {
  let observed;
  assert.deepEqual(await readLayout('1', async (url, options) => {
    observed = { url, options }; return json(snapshot);
  }), { state: 'ready', value: snapshot });
  assert.equal(observed.url, '/api/workspace/layout/');
  assert.equal(observed.options.credentials, 'same-origin');
  assert.equal(observed.options.cache, 'no-store');
  assert.equal(observed.options.redirect, 'error');
  assert.ok(observed.options.signal instanceof AbortSignal);
  for (const response of [json({ ...snapshot, user_id: '2' }), json({ ...snapshot, split_basis_points: 4501 }),
    json({ ...snapshot, revision: true }), json({ ...snapshot, layout_id: 'not-an-id' }),
    json({ ...snapshot, credential: 'private' }), json(snapshot, 503),
    new Response(' '.repeat(4097)), new Response(JSON.stringify(snapshot), { headers: { 'Content-Type': 'text/html' } })]) {
    assert.deepEqual(await readLayout('1', async () => response), { state: 'unavailable' });
  }
  assert.deepEqual(await readLayout('1', async () => { throw Error('private endpoint'); }), { state: 'unavailable' });
});

test('layout writes use fresh CSRF identity and exact revision and verify the acknowledged change', async () => {
  const calls = [];
  const result = await saveLayout(snapshot, 3375, async (url, options) => {
    calls.push({ url, options });
    return calls.length === 1 ? csrf() : json({ ...snapshot, revision: 1, split_basis_points: 3375 });
  });
  assert.equal(calls[0].url, '/api/session/csrf/');
  assert.equal(calls[1].url, '/api/workspace/layout/');
  assert.equal(calls[1].options.headers['X-CSRFToken'], 'a'.repeat(64));
  assert.equal(calls[1].options.headers['X-Maipa-Layout'], snapshot.layout_id);
  assert.deepEqual(JSON.parse(calls[1].options.body), { expected_revision: 0, split_basis_points: 3375 });
  assert.equal(calls[1].options.credentials, 'same-origin');
  assert.equal(calls[1].options.redirect, 'error');
  assert.deepEqual(result, { state: 'ready', value: { ...snapshot, revision: 1, split_basis_points: 3375 } });
  for (const response of [json({ ...snapshot, revision: 1, user_id: '2', split_basis_points: 3375 }),
    json({ ...snapshot, revision: 2, split_basis_points: 3375 }), json({ ...snapshot, revision: 1 }),
    json({ ...snapshot, revision: 1, layout_id: '51525eda-df14-4e4b-a369-4f8c20dc95ed', split_basis_points: 3375 }),
    json({}, 503)]) {
    assert.deepEqual(await saveLayout(snapshot, 3375, async url => url.includes('/csrf/') ? csrf() : response), { state: 'unavailable' });
  }
  assert.deepEqual(await saveLayout(snapshot, 3375, async url => url.includes('/csrf/') ? csrf() : json({}, 409)), { state: 'conflict' });
});

test('layout gesture state saves once and unknown outcome requires an explicit reread', async () => {
  const observed = [];
  let writes = 0;
  const controller = new LayoutController('1', value => observed.push(value), async (url, options) => {
    if (url.includes('/csrf/')) return csrf();
    if (options?.method === 'POST') { writes++; throw Error('lost acknowledgement'); }
    return json(snapshot);
  });
  await controller.load();
  controller.change(33.75);
  assert.equal(controller.current.split, 33.75);
  await controller.commit(33.75);
  assert.equal(controller.current.phase, 'unavailable');
  await controller.commit(40);
  assert.equal(writes, 1);
  assert.equal(controller.current.split, 33.75);
  await controller.load();
  assert.equal(controller.current.phase, 'ready');
  assert.equal(controller.current.split, 28);
  assert.ok(observed.some(value => value.phase === 'saving'));
});

test('stale loads and disposed identities cannot publish another users saved width', async () => {
  const pending = [];
  const observed = [];
  const controller = new LayoutController('1', value => observed.push(value), () => new Promise(resolve => pending.push(resolve)));
  const first = controller.load();
  const second = controller.load();
  pending[1](json({ ...snapshot, revision: 1, split_basis_points: 3800 }));
  await second;
  assert.equal(controller.current.split, 38);
  pending[0](json(snapshot));
  await first;
  assert.equal(controller.current.split, 38);
  const late = controller.load();
  controller.dispose();
  const count = observed.length;
  pending[2](json({ ...snapshot, revision: 2, split_basis_points: 4500 }));
  await late;
  assert.equal(observed.length, count);
  const other = new LayoutController('2', () => {}, async () => json(snapshot));
  await other.load();
  assert.equal(other.current.phase, 'unavailable');
  assert.equal(other.current.split, 28);
});

test('a pending commit locks further writes and stale acknowledgements after reload are ignored', async () => {
  let finish;
  let writes = 0;
  const controller = new LayoutController('1', () => {}, async (url, options) => {
    if (url.includes('/csrf/')) return csrf();
    if (options?.method === 'POST') { writes++; return new Promise(resolve => { finish = resolve; }); }
    return json(snapshot);
  });
  await controller.load();
  const save = controller.commit(33);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(controller.current.phase, 'saving');
  controller.change(44);
  await controller.commit(44);
  assert.equal(writes, 1);
  assert.equal(controller.current.split, 33);
  await controller.load();
  finish(json({ ...snapshot, revision: 1, split_basis_points: 3300 }));
  await save;
  assert.equal(controller.current.phase, 'ready');
  assert.equal(controller.current.split, 28);
});
