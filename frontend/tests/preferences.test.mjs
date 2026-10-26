import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { PreferencesController, readPreferences, savePreferences } from '../.test-build/preferences.js';
import { UserPanelModel } from '../.test-build/user-panel.js';
import { Workspace } from '../.test-build/App.js';

const snapshot = { preferences_id: '61525eda-df14-4e4b-a369-4f8c20dc95ed', user_id: '1', revision: 0, theme: 'system', active_view: 'client-facts' };
const choice = { theme: 'dark', active_view: 'workspace-guide' };
const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const csrf = () => json({ csrf_token: 'a'.repeat(64) });
const turn = () => new Promise(resolve => setImmediate(resolve));

test('personal preference reads are bounded same-origin and reject foreign or unsupported state', async () => {
  let observed;
  assert.deepEqual(await readPreferences('1', async (url, options) => {
    observed = { url, options }; return json(snapshot);
  }), { state: 'ready', value: snapshot });
  assert.equal(observed.url, '/api/workspace/preferences/');
  assert.equal(observed.options.credentials, 'same-origin');
  assert.equal(observed.options.cache, 'no-store');
  assert.equal(observed.options.redirect, 'error');
  assert.ok(observed.options.signal instanceof AbortSignal);
  for (const response of [json({ ...snapshot, user_id: '2' }), json({ ...snapshot, theme: 'auto' }),
    json({ ...snapshot, active_view: '../private' }), json({ ...snapshot, revision: true }),
    json({ ...snapshot, preferences_id: 'invalid' }), json({ ...snapshot, token: 'private' }),
    json(snapshot, 503), new Response(' '.repeat(4097)),
    new Response(JSON.stringify(snapshot), { headers: { 'Content-Type': 'text/html' } })]) {
    assert.deepEqual(await readPreferences('1', async () => response), { state: 'unavailable' });
  }
});

test('preference writes send exact choices identity revision and fresh CSRF and verify the acknowledgement', async () => {
  const calls = [];
  const result = await savePreferences(snapshot, choice, async (url, options) => {
    calls.push({ url, options }); return calls.length === 1 ? csrf() : json({ ...snapshot, ...choice, revision: 1 });
  });
  assert.deepEqual(calls.map(call => call.url), ['/api/session/csrf/', '/api/workspace/preferences/']);
  assert.equal(calls[1].options.headers['X-CSRFToken'], 'a'.repeat(64));
  assert.equal(calls[1].options.headers['X-Maipa-Preferences'], snapshot.preferences_id);
  assert.deepEqual(JSON.parse(calls[1].options.body), { expected_revision: 0, ...choice });
  assert.deepEqual(result, { state: 'ready', value: { ...snapshot, ...choice, revision: 1 } });
  for (const call of calls) {
    assert.equal(call.options.credentials, 'same-origin');
    assert.equal(call.options.redirect, 'error');
    assert.equal(call.options.cache, 'no-store');
    assert.ok(call.options.signal instanceof AbortSignal);
  }
  for (const value of [{ ...snapshot, ...choice, revision: 2 }, { ...snapshot, ...choice, revision: 1, user_id: '2' },
    { ...snapshot, ...choice, revision: 1, preferences_id: '51525eda-df14-4e4b-a369-4f8c20dc95ed' },
    { ...snapshot, revision: 1 }]) {
    assert.deepEqual(await savePreferences(snapshot, choice, async url => url.includes('/csrf/') ? csrf() : json(value)), { state: 'unavailable' });
  }
});

test('invalid preference fields and unsafe HTTP outcomes never become accepted settings', async () => {
  let calls = 0;
  for (const value of [{ ...choice, theme: 'auto' }, { ...choice, active_view: 'unknown' }, { ...choice, token: 'private' }]) {
    assert.deepEqual(await savePreferences(snapshot, value, async () => { calls++; }), { state: 'unavailable' });
  }
  assert.equal(calls, 0);
  for (const status of [401, 403, 404, 500, 503]) {
    assert.deepEqual(await savePreferences(snapshot, choice, async url => url.includes('/csrf/') ? csrf() : json({}, status)), { state: 'unavailable' });
  }
  assert.deepEqual(await savePreferences(snapshot, choice, async url => url.includes('/csrf/') ? csrf() : json({}, 409)), { state: 'conflict' });
});

test('an unknown preference save pauses writes until an explicit authoritative reload', async () => {
  let writes = 0;
  const seen = [];
  const controller = new PreferencesController('1', value => seen.push(value), async (url, options) => {
    if (url.includes('/csrf/')) return csrf();
    if (options?.method === 'POST') { writes++; throw Error('private diagnostic'); }
    return json(snapshot);
  });
  await controller.load();
  await controller.save(choice);
  assert.equal(controller.current.phase, 'unavailable');
  await controller.save({ theme: 'light' });
  assert.equal(writes, 1);
  assert.equal(controller.current.theme, 'dark');
  await controller.load();
  assert.deepEqual(controller.current, { phase: 'ready', theme: 'system', active_view: 'client-facts' });
  assert.ok(seen.some(value => value.phase === 'saving'));
  assert.doesNotMatch(JSON.stringify(seen), /private diagnostic/);
});

test('late preference loads and disposed identities cannot publish into another principal', async () => {
  const pending = [], seen = [];
  const controller = new PreferencesController('1', value => seen.push(value), () => new Promise(resolve => pending.push(resolve)));
  const first = controller.load(), second = controller.load();
  pending[1](json({ ...snapshot, ...choice, revision: 1 })); await second;
  pending[0](json(snapshot)); await first;
  assert.equal(controller.current.theme, 'dark');
  const late = controller.load();
  controller.dispose();
  const count = seen.length;
  pending[2](json(snapshot)); await late;
  assert.equal(seen.length, count);
  const other = new PreferencesController('2', () => {}, async () => json(snapshot));
  await other.load();
  assert.equal(other.current.phase, 'unavailable');
  assert.equal(other.current.theme, 'system');
});

test('pending preference writes lock further mutations and stale acknowledgements cannot overwrite a reload', async () => {
  let finish, writes = 0;
  const controller = new PreferencesController('1', () => {}, async (url, options) => {
    if (url.includes('/csrf/')) return csrf();
    if (options?.method === 'POST') { writes++; return new Promise(resolve => { finish = resolve; }); }
    return json(snapshot);
  });
  await controller.load();
  const saving = controller.save(choice); await turn();
  assert.equal(controller.current.phase, 'saving');
  await controller.save({ theme: 'light' });
  assert.equal(writes, 1);
  await controller.load();
  finish(json({ ...snapshot, ...choice, revision: 1 })); await saving;
  assert.deepEqual(controller.current, { phase: 'ready', theme: 'system', active_view: 'client-facts' });
});

test('human and published screen navigation share the preference boundary and restore never writes', async () => {
  const chosen = [];
  let ready = false;
  const model = new UserPanelModel('1', id => { if (!ready) return false; chosen.push(id); return true; });
  assert.equal((await model.server.callTool('screens_navigate', { screen: 'workspace-guide' })).isError, true);
  assert.equal(model.current.active, 'client-facts');
  ready = true;
  model.navigate('workspace-guide');
  assert.equal(model.current.active, 'workspace-guide');
  await model.server.callTool('screens_navigate', { screen: 'client-facts' });
  assert.deepEqual(chosen, ['workspace-guide', 'client-facts']);
  model.restoreBuiltin('workspace-guide');
  assert.equal(model.current.active, 'workspace-guide');
  assert.deepEqual(chosen, ['workspace-guide', 'client-facts']);
  assert.throws(() => model.restoreBuiltin('untrusted-view'));
  model.dispose();
});

test('authenticated workspace exposes loading and recovery state before allowing preference edits', () => {
  const html = renderToStaticMarkup(React.createElement(Workspace, { session: { state: 'authenticated', userId: '1' }, refresh() {} }));
  assert.match(html, /data-preferences-state="loading"/);
  assert.match(html, /Loading your appearance and view/);
  const dark = html.match(/<button\b[^>]*id="maipa-theme-dark"[^>]*>/)?.[0];
  assert.ok(dark);
  assert.match(dark, /\bdisabled=""/);
  assert.doesNotMatch(html, /Connected to (Claude|Codex)|provider_ready.*true/);
});
