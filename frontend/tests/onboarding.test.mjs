import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { FirstRunForm } from '../.test-build/FirstRun.js';
import { SetupReview } from '../.test-build/AccountSetupChat.js';
import { readFirstRun, createAdmin, readOnboarding, answerOnboarding } from '../.test-build/onboarding.js';

const token = 'c'.repeat(64);
const fixture = { setup_id: '12345678-1234-4234-8234-123456789012', revision: 7, step: 'review', draft: { display_name: 'Morgan', workspace: 'new', entity_name: '<unsafe entity>',
  engagement_name: 'Synthetic work', currency: 'USD', reporting_basis: null, effective_from: '2026-10-01' },
  messages: [{ role: 'guide', text: 'Review before saving.' }], choices: [], entity_id: null,
  deferred: false, provider_ready: false, can_edit: true };

test('first-run adapter and form distinguish local administrator creation from account setup', async () => {
  assert.equal(await readFirstRun(async () => new Response(JSON.stringify({ setup_required: true }))), true);
  assert.equal(await readFirstRun(async () => new Response(JSON.stringify({ setup_required: false }))), false);
  const html = renderToStaticMarkup(React.createElement(FirstRunForm, { refresh() {} }));
  assert.match(html, /Create local administrator/);
  assert.match(html, /runtime\/local-setup-code/);
  assert.match(html, /autoComplete="new-password"/);
  assert.match(html, /Confirm password/);
  assert.doesNotMatch(html, /Connected to (Claude|Codex)|value="[a-f0-9]{64}"/);
});

test('administrator creation uses CSRF and bounded same-origin requests without retaining credentials', async () => {
  const calls = [];
  const result = await createAdmin('a'.repeat(64), 'owner', 'synthetic-strong-passphrase', 'synthetic-strong-passphrase', async (url, options) => {
    calls.push({ url, options });
    return new Response(JSON.stringify(calls.length === 1 ? { csrf_token: token } : { user: { id: '19' } }), { status: calls.length === 1 ? 200 : 201 });
  });
  assert.equal(result, 'created');
  assert.deepEqual(calls.map(call => call.url), ['/api/session/csrf/', '/api/setup/']);
  assert.equal(calls[1].options.headers['X-CSRFToken'], token);
  assert.deepEqual(JSON.parse(calls[1].options.body), { setup_code: 'a'.repeat(64), username: 'owner', password: 'synthetic-strong-passphrase', password_confirmation: 'synthetic-strong-passphrase' });
  for (const call of calls) {
    assert.equal(call.options.credentials, 'same-origin');
    assert.equal(call.options.cache, 'no-store');
    assert.equal(call.options.redirect, 'error');
    assert.ok(call.options.signal instanceof AbortSignal);
  }
  assert.doesNotMatch(JSON.stringify(result), /synthetic|aaaaa|ccccc/);
});

test('invalid setup and failed bootstrap cannot claim success or disclose server diagnostics', async () => {
  assert.equal(await readFirstRun(async () => new Response('{}')), null);
  assert.equal(await readFirstRun(async () => { throw Error('private path'); }), null);
  let calls = 0;
  assert.equal(await createAdmin('bad', 'owner', 'short', 'different', async () => { calls++; }), 'invalid');
  assert.equal(calls, 0);
  for (const [status, expected] of [[400, 'invalid'], [403, 'invalid_code'], [409, 'closed'], [503, 'unavailable']]) {
    calls = 0;
    assert.equal(await createAdmin('a'.repeat(64), 'owner', 'synthetic-strong-passphrase', 'synthetic-strong-passphrase', async () =>
      ++calls === 1 ? new Response(JSON.stringify({ csrf_token: token })) : new Response('private diagnostic', { status })), expected);
  }
});

test('chat adapter sends only revision action and answer and renders an escaped explicit review', async () => {
  const calls = [];
  const result = await answerOnboarding(fixture.setup_id, 7, 'confirm', 'reply', async (url, options) => {
    calls.push({ url, options });
    return new Response(JSON.stringify(calls.length === 1 ? { csrf_token: token } : fixture));
  });
  assert.equal(result.state, 'ready');
  assert.deepEqual(calls.map(call => call.url), ['/api/session/csrf/', '/api/onboarding/']);
  assert.deepEqual(JSON.parse(calls[1].options.body), { expected_revision: 7, answer: 'confirm', action: 'reply' });
  assert.equal(calls[1].options.headers['X-CSRFToken'], token);
  assert.equal(calls[1].options.headers['X-Maipa-Setup'], fixture.setup_id);
  const html = renderToStaticMarkup(React.createElement(SetupReview, { value: result.value }));
  assert.match(html, /Unknown for now/);
  assert.match(html, /&lt;unsafe entity&gt;/);
  assert.match(html, /2026-10-01/);
  assert.doesNotMatch(html, /<unsafe entity>/);
});

test('chat errors and malformed or falsely connected responses never become accepted setup state', async () => {
  for (const [status, state] of [[401, 'denied'], [403, 'denied'], [404, 'denied'], [409, 'conflict'], [500, 'unavailable']]) {
    assert.deepEqual(await readOnboarding(async () => new Response('private', { status })), { state });
  }
  for (const value of [{}, { ...fixture, revision: -1 }, { ...fixture, provider_ready: true },
    { ...fixture, messages: [{ role: 'system', text: 'invented approval' }] }, { ...fixture, draft: { role: 'administrator' } }]) {
    assert.deepEqual(await readOnboarding(async () => new Response(JSON.stringify(value))), { state: 'unavailable' });
  }
  assert.deepEqual(await answerOnboarding(fixture.setup_id, -1, 'confirm'), { state: 'invalid' });
});
