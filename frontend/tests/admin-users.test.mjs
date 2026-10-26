import assert from 'node:assert/strict';
import { test } from 'node:test';
import { canAdminister, readAdminUsers, saveLocalUser } from '../.test-build/admin-users.js';

const snapshot = { actor_id: '1', installation_id: '12345678-1234-4234-8234-123456789012', revision: 7,
  users: [{ id: '1', username: 'local-admin', role: 'administrator', enabled: true, active: true }], events: [] };
const response = value => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });

test('administrator reads only a bounded snapshot bound to the mounted actor', async () => {
  const calls = [];
  const result = await readAdminUsers('1', async (url, options) => { calls.push({ url, options }); return response(snapshot); });
  assert.deepEqual(result, { kind: 'ok', value: snapshot });
  assert.equal(calls[0].url, '/api/admin/users/');
  assert.equal(calls[0].options.credentials, 'same-origin');
  assert.equal(calls[0].options.cache, 'no-store');
  assert.equal(calls[0].options.redirect, 'error');
  assert.ok(calls[0].options.signal instanceof AbortSignal);
  assert.equal(await canAdminister(async () => response({ installation_id: snapshot.installation_id,
    role: 'administrator', permissions: ['workspace.read'] })), true);
});

test('foreign malformed private or oversized access data never enters the UI model', async () => {
  for (const bad of [null, { ...snapshot, actor_id: '2' }, { ...snapshot, revision: true },
    { ...snapshot, password: 'synthetic-private' }, { ...snapshot, users: [...snapshot.users, ...snapshot.users] },
    { ...snapshot, users: [{ ...snapshot.users[0], role: 'owner' }] },
    { ...snapshot, users: [{ ...snapshot.users[0], password: 'synthetic-private' }] },
    { ...snapshot, events: [{ actor_id: '1' }] }, { ...snapshot, padding: 'x'.repeat(131073) }]) {
    assert.deepEqual(await readAdminUsers('1', async () => response(bad)), { kind: 'unavailable' });
  }
  for (const status of [401, 403]) assert.deepEqual(await readAdminUsers('1', async () => new Response('', { status })), { kind: 'denied' });
  assert.deepEqual(await readAdminUsers('1', async () => new Response(' '.repeat(262145) + JSON.stringify(snapshot),
    { headers: { 'Content-Type': 'application/json' } })), { kind: 'unavailable' });
  for (const role of ['preparer', 'viewer', 'reviewer', 'owner'])
    assert.equal(await canAdminister(async () => response({ installation_id: snapshot.installation_id, role, permissions: ['workspace.read'] })), false);
});

test('create and role writes bind actor install revision and csrf and acknowledge the exact next revision', async () => {
  const calls = [];
  const fetcher = async (url, options) => {
    calls.push({ url, options });
    return url === '/api/session/csrf/' ? response({ csrf_token: 'A'.repeat(64) })
      : new Response(JSON.stringify({ status: 'saved', revision: 8 }), { status: url === '/api/admin/users/' ? 201 : 200, headers: { 'Content-Type': 'application/json' } });
  };
  const choices = { username: 'new-local', password: 'synthetic-only-password', confirmation: 'synthetic-only-password', role: 'viewer' };
  assert.deepEqual(await saveLocalUser(snapshot, null, choices, fetcher), { kind: 'ok', value: 8 });
  const created = calls[1];
  assert.equal(created.url, '/api/admin/users/');
  assert.equal(created.options.method, 'POST');
  assert.deepEqual(created.options.headers, { 'Content-Type': 'application/json', 'X-CSRFToken': 'A'.repeat(64), 'X-Maipa-Admin': '1' });
  assert.deepEqual(JSON.parse(created.options.body), { expected_revision: 7, installation_id: snapshot.installation_id, ...choices });
  assert.deepEqual(await saveLocalUser(snapshot, '2', { role: 'reviewer', enabled: false }, fetcher), { kind: 'ok', value: 8 });
  assert.equal(calls[3].url, '/api/admin/users/2/');
});

test('conflicts denial bad receipts and authority-shaped choices never claim a saved change', async () => {
  for (const [status, kind] of [[400, 'invalid'], [401, 'denied'], [403, 'denied'], [409, 'conflict'], [503, 'unavailable']]) {
    const fetcher = async url => url === '/api/session/csrf/' ? response({ csrf_token: 'A'.repeat(64) }) : new Response('', { status });
    assert.deepEqual(await saveLocalUser(snapshot, '2', { role: 'viewer', enabled: true }, fetcher), { kind });
  }
  for (const value of [{ status: 'saved', revision: 7 }, { status: 'saved', revision: 9 }, { status: 'saved', revision: 8, token: 'private' }]) {
    const fetcher = async url => response(url === '/api/session/csrf/' ? { csrf_token: 'A'.repeat(64) } : value);
    assert.deepEqual(await saveLocalUser(snapshot, '2', { role: 'viewer', enabled: true }, fetcher), { kind: 'unavailable' });
  }
  let calls = 0;
  const fetcher = async () => { calls++; throw Error('must not send'); };
  for (const target of ['../2', 'https://foreign.invalid', '2/3'])
    assert.deepEqual(await saveLocalUser(snapshot, target, { role: 'viewer', enabled: true }, fetcher), { kind: 'unavailable' });
  assert.deepEqual(await saveLocalUser(snapshot, '2', { role: 'viewer', enabled: true, is_superuser: true }, fetcher), { kind: 'unavailable' });
  assert.equal(calls, 0);
});
