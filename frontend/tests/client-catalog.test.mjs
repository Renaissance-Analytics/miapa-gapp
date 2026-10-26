import assert from 'node:assert/strict';
import { test } from 'node:test';
import { canManageClients, readClientCatalog, saveClientCatalog } from '../.test-build/client-catalog.js';
import { UserPanelModel, validateWorkSchema } from '../.test-build/user-panel.js';

const group = '11111111-1111-4111-8111-111111111111';
const entity = '22222222-2222-4222-8222-222222222222';
const other = '33333333-3333-4333-8333-333333333333';
const snapshot = { actor_id: '1', installation_id: '44444444-4444-4444-8444-444444444444',
  identity: '55555555-5555-4555-8555-555555555555', revision: 0,
  engagements: [{ id: group, name: 'Synthetic client', entity_count: 1 }], selected_engagement_id: group,
  entities: [{ id: entity, name: 'Synthetic entity' }], events: [] };
const response = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const receipt = (command, target = entity, engagement = group) => ({ status: 'saved', actor_id: snapshot.actor_id,
  identity: snapshot.identity, installation_id: snapshot.installation_id, revision: snapshot.revision+1,
  action: command.action, target_id: target, engagement_id: engagement });

test('catalog reads bind the mounted actor selected engagement and bounded private response', async () => {
  const calls = [];
  const result = await readClientCatalog('1', group, async (url, options) => { calls.push({ url, options }); return response(snapshot); });
  assert.deepEqual(result, { kind: 'ok', value: snapshot });
  assert.equal(calls[0].url, `/api/intake/catalog/?engagement_id=${group}`);
  assert.equal(calls[0].options.credentials, 'same-origin');
  assert.equal(calls[0].options.cache, 'no-store');
  assert.equal(calls[0].options.redirect, 'error');
  assert.ok(calls[0].options.signal instanceof AbortSignal);
  const empty = { ...snapshot, engagements: [], selected_engagement_id: null, entities: [] };
  assert.deepEqual(await readClientCatalog('1', undefined, async () => response(empty)), { kind: 'ok', value: empty });
});

test('foreign malformed duplicate and oversized catalog snapshots never enter UI state', async () => {
  for (const bad of [null, { ...snapshot, actor_id: '2' }, { ...snapshot, revision: true },
    { ...snapshot, home: 'C:/private' }, { ...snapshot, selected_engagement_id: other },
    { ...snapshot, entities: [...snapshot.entities, ...snapshot.entities] },
    { ...snapshot, engagements: [...snapshot.engagements, ...snapshot.engagements] },
    { ...snapshot, entities: [{ id: entity, name: 'x', token: 'private' }] },
    { ...snapshot, engagements: [{ id: group, name: 'x', entity_count: 2 }] },
    { ...snapshot, engagements: [{ id: group, name: 'x', entity_count: 101 }] },
    { ...snapshot, events: [{ action: 'run_command' }] }]) {
    assert.deepEqual(await readClientCatalog('1', group, async () => response(bad)), { kind: 'unavailable' });
  }
  assert.deepEqual(await readClientCatalog('1', group, async () => new Response(' '.repeat(262145)+JSON.stringify(snapshot),
    { headers: { 'Content-Type': 'application/json' } })), { kind: 'unavailable' });
  let calls = 0;
  assert.deepEqual(await readClientCatalog('1', '../private', async () => { calls++; }), { kind: 'unavailable' });
  assert.equal(calls, 0);
});

test('catalog writes send only the closed command and bind csrf original actor identity install and revision', async () => {
  const choices = [{ action: 'create_engagement', name: 'New client', entity_name: 'New entity' },
    { action: 'create_entity', engagement_id: group, name: 'New entity' },
    { action: 'rename_engagement', engagement_id: group, name: 'New client name' },
    { action: 'rename_entity', entity_id: entity, name: 'New entity name' }];
  for (const command of choices) {
    const calls = [];
    const target = command.action.startsWith('create_') ? other : command.action.endsWith('engagement') ? group : entity;
    const expected = receipt(command, target, command.action === 'create_engagement' ? other : group);
    const fetcher = async (url, options) => {
      calls.push({ url, options });
      return url === '/api/session/csrf/' ? response({ csrf_token: 'A'.repeat(64) })
        : response(expected, command.action.startsWith('create_') ? 201 : 200);
    };
    assert.deepEqual(await saveClientCatalog(snapshot, command, fetcher), { kind: 'ok', value: expected });
    assert.equal(calls.length, 2);
    assert.equal(calls[1].url, '/api/intake/catalog/');
    assert.equal(calls[1].options.method, 'POST');
    assert.deepEqual(calls[1].options.headers, { 'Content-Type': 'application/json', 'X-CSRFToken': 'A'.repeat(64), 'X-Maipa-Client-Actor': '1' });
    assert.deepEqual(JSON.parse(calls[1].options.body), { identity: snapshot.identity,
      installation_id: snapshot.installation_id, expected_revision: snapshot.revision, ...command });
  }
});

test('stale denied unconfirmed and mismatched catalog receipts never claim success or retry a write', async () => {
  const command = { action: 'rename_entity', entity_id: entity, name: 'Chosen name' };
  for (const [status, kind] of [[400, 'invalid'], [401, 'denied'], [403, 'denied'], [404, 'conflict'], [409, 'conflict'], [503, 'unavailable']]) {
    let writes = 0;
    const fetcher = async url => url === '/api/session/csrf/' ? response({ csrf_token: 'A'.repeat(64) })
      : (++writes, new Response('', { status }));
    assert.deepEqual(await saveClientCatalog(snapshot, command, fetcher), { kind });
    assert.equal(writes, 1);
  }
  const good = receipt(command);
  for (const bad of [{ ...good, actor_id: '2' }, { ...good, identity: other }, { ...good, installation_id: other },
    { ...good, revision: 0 }, { ...good, revision: 2 }, { ...good, target_id: other },
    { ...good, engagement_id: other }, { ...good, action: 'create_entity' }, { ...good, password: 'private' }]) {
    const fetcher = async url => response(url === '/api/session/csrf/' ? { csrf_token: 'A'.repeat(64) } : bad);
    assert.deepEqual(await saveClientCatalog(snapshot, command, fetcher), { kind: 'unavailable' });
  }
});

test('unknown targets authority fields unsafe names and unsupported commands are refused before network use', async () => {
  let calls = 0;
  const fetcher = async () => { calls++; throw Error('must not send'); };
  for (const bad of [{ action: 'rename_entity', entity_id: other, name: 'Unknown target' },
    { action: 'create_entity', engagement_id: '../private', name: 'x' },
    { action: 'rename_engagement', engagement_id: other, name: 'Unknown target' },
    { action: 'create_engagement', name: '', entity_name: 'x' },
    { action: 'create_engagement', name: 'x'.repeat(129), entity_name: 'x' },
    { action: 'create_engagement', name: 'x', entity_name: 'x', owner_id: '2' },
    { action: 'create_engagement', name: 'x', entity_name: 'x', home: 'C:/private' },
    { action: 'run', command: 'never-run' }]) {
    assert.deepEqual(await saveClientCatalog(snapshot, bad, fetcher), { kind: 'unavailable' });
  }
  assert.equal(calls, 0);
});

test('client administration discovery admits preparers and administrators with actual server authority still required', async () => {
  for (const role of ['preparer', 'administrator'])
    assert.equal(await canManageClients(async () => response({ installation_id: snapshot.installation_id, role, permissions: ['workspace.read'] })), true);
  for (const role of ['reviewer', 'viewer', 'owner'])
    assert.equal(await canManageClients(async () => response({ installation_id: snapshot.installation_id, role, permissions: ['workspace.read'] })), false);
  assert.equal(await canManageClients(async () => response({ role: 'preparer', home: 'C:/private' })), false);
  assert.equal(await canManageClients(async () => new Response('', { status: 403 })), false);
});

test('client setup is a reserved optional shared screen which preserves saved view choices and principal scope', async () => {
  const saved = [];
  const model = new UserPanelModel('1', id => { saved.push(id); return true; });
  const foreign = new UserPanelModel('2');
  try {
    assert.equal((await model.server.callTool('screens_navigate', { screen: 'client-setup' })).isError, true);
    assert.equal((await model.server.callTool('screens_create', { id: 'client-setup', kind: 'maipa-composition',
      config: { schema: { id: 'fake-admin', type: 'Maipa.Note', props: { text: 'Impersonation' } } } })).isError, true);
    model.enableClientAdministration(); model.enableClientAdministration();
    assert.equal(model.current.views.filter(view => view.id === 'client-setup').length, 1);
    assert.notEqual((await model.server.callTool('screens_navigate', { screen: 'client-setup' })).isError, true);
    assert.equal(model.current.active, 'client-setup');
    assert.deepEqual(saved, []);
    assert.equal((await foreign.server.callTool('screens_navigate', { screen: 'client-setup' })).isError, true);
    assert.throws(() => validateWorkSchema({ id: 'fake', type: 'Maipa.ClientCatalog' }));
    assert.equal((await model.server.callTool('screens_destroy', { id: 'client-setup' })).isError, true);
    assert.equal((await model.server.callTool('screens_update_content', { id: 'client-setup', partial: { schema: { id: 'x', type: 'Maipa.Note', props: { text: 'x' } } } })).isError, true);
    model.navigate('workspace-guide');
    assert.deepEqual(saved, ['workspace-guide']);
  } finally { model.dispose(); foreign.dispose(); }
  assert.equal(model.server.listTools().length, 0);
});
