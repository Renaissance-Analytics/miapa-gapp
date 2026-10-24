import assert from 'node:assert/strict';
import test from 'node:test';
import * as authority from '../.test-build/authority.js';

const identity = '11111111-1111-4111-8111-111111111111';
const token = 'a'.repeat(64);
const grant = { id: identity, action: 'ledger.post', duty: 'approver', limit: '1000.0000', currency: 'USD',
  starts_at: '2026-01-01T00:00:00Z', expires_at: '2027-01-01T00:00:00Z', evidence: 'Synthetic signed authority',
  parent_id: null, can_delegate: true, current: true, revoked_at: null, actor_kind: 'human' };
const summary = { entity_id: identity, policy_version: 1, financial_execution_available: false, grants: [grant], next_before: null };

test('authority reads preserve exact decimal evidence and current server scope', async () => {
  const calls = [];
  const result = await authority.readAuthority(identity, undefined, async (url, options) => {
    calls.push({ url, options }); return new Response(JSON.stringify(summary));
  });
  assert.deepEqual(result, { kind: 'ok', value: summary });
  assert.equal(calls[0].url, `/api/policies/entities/${identity}/`);
  assert.equal(calls[0].options.credentials, 'same-origin');
  assert.equal(calls[0].options.cache, 'no-store');
  assert.equal(calls[0].options.redirect, 'error');
  assert.ok(calls[0].options.signal instanceof AbortSignal);
});

test('authority rejects foreign scope malformed evidence and unexpected execution readiness', async () => {
  for (const value of [null, {}, { ...summary, entity_id: '22222222-2222-4222-8222-222222222222' },
    { ...summary, financial_execution_available: true }, { ...summary, grants: [{ ...grant, limit: 1000 }] },
    { ...summary, grants: [{ ...grant, current: 'true' }] }, { ...summary, grants: [{ ...grant, duty: 'invented' }] }]) {
    assert.deepEqual(await authority.readAuthority(identity, undefined, async () => new Response(JSON.stringify(value))), { kind: 'unavailable' });
  }
  let calls = 0;
  assert.deepEqual(await authority.readAuthority('../private', undefined, async () => { calls++; }), { kind: 'unavailable' });
  assert.equal(calls, 0);
});

test('authority revocation sends only the reason through fresh csrf and does not invent success', async () => {
  const calls = [];
  const result = await authority.revokeAuthority(identity, 'Synthetic permission withdrawn', async (url, options) => {
    calls.push({ url, options }); return new Response(JSON.stringify(calls.length === 1 ? { csrf_token: token } : { status: 'revoked' }));
  });
  assert.deepEqual(result, { kind: 'ok' });
  assert.deepEqual(calls.map(call => call.url), ['/api/session/csrf/', `/api/policies/grants/${identity}/revoke/`]);
  assert.deepEqual(JSON.parse(calls[1].options.body), { reason: 'Synthetic permission withdrawn' });
  assert.equal(calls[1].options.headers['X-CSRFToken'], token);
  assert.equal(calls[1].options.method, 'POST');
  assert.equal(calls[1].options.credentials, 'same-origin');
  assert.equal(calls[1].options.redirect, 'error');
});

test('authority errors are fixed and uncertain mutation responses require reload', async () => {
  for (const [status, kind] of [[401, 'signed_out'], [403, 'denied'], [404, 'denied'], [409, 'conflict'], [503, 'unavailable']]) {
    assert.deepEqual(await authority.readAuthority(identity, undefined, async () => new Response('private detail', { status })), { kind });
  }
  let calls = 0;
  assert.deepEqual(await authority.revokeAuthority(identity, 'Synthetic permission withdrawn', async () => {
    calls++; return new Response(JSON.stringify(calls === 1 ? { csrf_token: token } : { status: 'unknown' }));
  }), { kind: 'unavailable' });
  assert.equal(calls, 2);
  calls = 0;
  assert.deepEqual(await authority.revokeAuthority(identity, 'Synthetic', async () => {
    calls++; return new Response(JSON.stringify({ csrf_token: 'unsafe\nvalue' }));
  }), { kind: 'unavailable' });
  assert.equal(calls, 1);
});

test('authority pagination binds the exact server cursor and rejects invented paths', async () => {
  let requested;
  const result = await authority.readAuthority(identity, identity, async url => {
    requested = url; return new Response(JSON.stringify({ ...summary, grants: [], next_before: null }));
  });
  assert.equal(result.kind, 'ok');
  assert.equal(requested, `/api/policies/entities/${identity}/?before=${identity}`);
  let calls = 0;
  assert.deepEqual(await authority.readAuthority(identity, '../foreign', async () => { calls++; }), { kind: 'unavailable' });
  assert.equal(calls, 0);
});
