import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { AgentSurface } from '../.test-build/AgentSurface.js';
import { compositionSchema } from '../.test-build/agent-chat.js';
import { SharedUiBridge } from '../.test-build/shared-ui-bridge.js';

const schema = { id: 'review-root', type: 'Maipa.Stack', children: [
  { id: 'review-note', type: 'Maipa.Note', props: { text: 'Check the source <script>untrusted</script>' } },
] };
test('Fancy renders the controlled authored composition without fixed navigation or executable text', () => {
  const ui = { account_id: 'account', id: 'lease', revision: 2, composition: { title: 'Review', schema: compositionSchema(schema) } };
  const html = renderToStaticMarkup(React.createElement(AgentSurface, { userId: '1', ui }));
  assert.match(html, /data-fancy-screens-screen/);
  assert.match(html, /composition-user-1/);
  assert.match(html, /&lt;script&gt;untrusted&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script|Work views|Workspace guide|work-view-controls/);
  assert.equal(renderToStaticMarkup(React.createElement(AgentSurface, { userId: '1', ui: { ...ui, composition: null } })), '');
});
test('composition validation refuses code selectors repeated controls duplicate IDs and oversized trees', () => {
  for (const invalid of [
    { id: 'x', type: 'iframe', props: { src: 'https://foreign.invalid' } },
    { id: 'x', type: 'Maipa.ClientFacts', props: { user_id: '2' } },
    { id: 'x', type: 'Maipa.Note', props: { text: 'x', dangerouslySetInnerHTML: { __html: '<script>' } } },
    { id: 'x', type: 'Maipa.Note', props: { text: 'x', onClick: 'execute()' } },
    { id: 'x', type: 'Maipa.Note', props: { text: 'x'.repeat(2001) } },
    { id: 'x', type: 'Maipa.Stack', children: [{ id: 'x', type: 'Maipa.ClientFacts' }] },
    { id: 'x', type: 'Maipa.Stack', children: [{ id: 'a', type: 'Maipa.ClientFacts' }, { id: 'b', type: 'Maipa.ClientFacts' }] },
    { type: 'Maipa.Note' }, { id: 'x', type: 'Maipa.Stack', children: Array(17).fill(schema) },
    { id: 'x', type: 'Maipa.WorkspaceGuide' },
  ]) assert.throws(() => compositionSchema(invalid));
  assert.deepEqual(compositionSchema({ id: 'users', type: 'Maipa.AdminUsers' }), { id: 'users', type: 'Maipa.AdminUsers' });
});
test('accepted composition is detached from caller-owned schema and preserves stable handles', () => {
  const original = structuredClone(schema);
  const accepted = compositionSchema(original);
  original.children[0].props.text = 'Changed after acceptance';
  assert.equal(accepted.children[0].props.text, schema.children[0].props.text);
  assert.equal(accepted.children[0].id, 'review-note');
});
test('published Human+ host awaits the owned server receipt and rejects stale foreign or disposed controls', async () => {
  const ui = { account_id: 'account', id: 'lease', revision: 2, composition: { title: 'Review', schema } };
  let finish, calls = 0;
  const host = new SharedUiBridge('1', () => ui, async (user, snapshot) => {
    calls++;
    assert.equal(user, '1'); assert.deepEqual(snapshot, ui);
    await new Promise(resolve => { finish = resolve; });
  });
  for (const args of [{ id: 'foreign', revision: 2 }, { id: 'lease', revision: 1 },
    { id: 'lease', revision: 2, user_id: '2' }]) assert.equal((await host.server.callTool('maipa_ui_dismiss', args)).isError, true);
  assert.equal(calls, 0);
  let done = false;
  const pending = host.dismiss(ui).then(() => { done = true; });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(done, false); assert.equal(calls, 1);
  finish(); await pending; assert.equal(done, true);
  host.dispose(); assert.equal(host.server.listTools().length, 0);
  await assert.rejects(() => host.dismiss(ui));
});
