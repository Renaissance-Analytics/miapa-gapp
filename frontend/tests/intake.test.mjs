import assert from 'node:assert/strict';
import test from 'node:test';
import * as intake from '../.test-build/intake.js';

const identity = '11111111-1111-4111-8111-111111111111';
const token = 'a'.repeat(64);
const draft = { key: 'records_complete', knowledge: 'known', value: false, expected_revision: 2,
  effective_from: '2026-01-01', effective_to: null,
  evidence: { reference: 'synthetic-source', locator: 'page 1', observed_on: '2026-10-24' } };
const revision = { ...draft, id: identity, revision_id: identity, revision: 3,
  recorded_by: '42', recorded_at: '2026-10-24T12:00:00Z' };

test('intake preserves known false and explicit unknown without inferring tax status', () => {
  assert.equal(intake.factPayload(draft).value, false);
  assert.deepEqual(intake.factPayload({ ...draft, knowledge: 'unknown', value: false }), { ...draft, knowledge: 'unknown', value: null });
  assert.deepEqual(Object.keys(intake.factPayload({ ...draft, owner: 'foreign', role: 'administrator' })).sort(),
    ['key', 'knowledge', 'value', 'expected_revision', 'effective_from', 'effective_to', 'evidence'].sort());
  assert.equal(intake.formatFact({ knowledge: 'known', value: false }), 'No');
  assert.equal(intake.formatFact({ knowledge: 'unknown', value: null }), 'Unknown');
  assert.equal(intake.formatFact({ knowledge: 'known', value: [] }), 'None recorded');
});

test('fact saves send exact optimistic evidence through protected same-origin requests', async () => {
  const calls = [];
  const result = await intake.saveFact(identity, draft, async (url, options) => {
    calls.push({ url, options });
    return new Response(JSON.stringify(calls.length === 1 ? { csrf_token: token } : revision), { status: calls.length === 1 ? 200 : 201 });
  });
  assert.equal(result.kind, 'ok');
  assert.equal(result.value.value, false);
  assert.deepEqual(calls.map(c => c.url), ['/api/session/csrf/', `/api/intake/entities/${identity}/facts/`]);
  assert.deepEqual(JSON.parse(calls[1].options.body), draft);
  assert.equal(calls[1].options.headers['X-CSRFToken'], token);
  assert.equal(calls[1].options.method, 'POST');
  for (const { options } of calls) {
    assert.equal(options.credentials, 'same-origin');
    assert.equal(options.cache, 'no-store');
    assert.equal(options.redirect, 'error');
    assert.ok(options.signal instanceof AbortSignal);
  }
});

test('intake conflicts denial failures and invalid csrf expose only fixed states', async () => {
  for (const [status, expected] of [[400, 'invalid'], [401, 'signed_out'], [403, 'denied'], [409, 'conflict'], [500, 'unavailable']]) {
    let calls = 0;
    const result = await intake.saveFact(identity, draft, async () => {
      calls++;
      return calls === 1 ? new Response(JSON.stringify({ csrf_token: token })) : new Response('private server detail', { status });
    });
    assert.deepEqual(result, { kind: expected });
    assert.equal(draft.expected_revision, 2);
    assert.equal(draft.value, false);
  }
  let calls = 0;
  assert.deepEqual(await intake.saveFact(identity, draft, async () => {
    calls++; return new Response(JSON.stringify({ csrf_token: 'bad\nheader' }));
  }), { kind: 'unavailable' });
  assert.equal(calls, 1);
});

test('intake reads reject malformed records and caller supplied resource paths', async () => {
  let calls = 0;
  assert.deepEqual(await intake.readEntity('../foreign?owner=x', '2026-10-24', async () => { calls++; }), { kind: 'unavailable' });
  assert.equal(calls, 0);
  for (const body of [null, {}, { engagements: [{ id: 'private-path', name: 'x', entities: [] }] }]) {
    assert.deepEqual(await intake.listEngagements(async () => new Response(JSON.stringify(body))), { kind: 'unavailable' });
  }
  assert.deepEqual(await intake.listEngagements(async () => { throw Error('private detail'); }), { kind: 'unavailable' });
  assert.deepEqual(await intake.listEngagements(async () => new Response('{bad')), { kind: 'unavailable' });
});

test('engagement creation and history retain separate entity and revision identities', async () => {
  const calls = [];
  const created = { id: identity, name: 'Synthetic engagement', entities: [{ id: identity, name: 'Synthetic entity', revision: 0 }] };
  const result = await intake.createEngagement('Synthetic engagement', 'Synthetic entity', async (url, options) => {
    calls.push({ url, options });
    return new Response(JSON.stringify(calls.length === 1 ? { csrf_token: token } : created), { status: calls.length === 1 ? 200 : 201 });
  });
  assert.deepEqual(result, { kind: 'ok', value: created });
  assert.deepEqual(JSON.parse(calls[1].options.body), { name: 'Synthetic engagement', entity_name: 'Synthetic entity' });
  let requested;
  const history = await intake.readHistory(identity, 'records_complete', 4, async (url) => {
    requested = url; return new Response(JSON.stringify({ revisions: [revision], next_before: null }));
  });
  assert.equal(requested, `/api/intake/entities/${identity}/facts/records_complete/history/?before=4`);
  assert.equal(history.value.revisions[0].revision_id, identity);
  assert.equal(history.value.revisions[0].value, false);
});
