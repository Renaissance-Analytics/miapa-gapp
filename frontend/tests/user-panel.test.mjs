import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { UserPanelModel, validateWorkSchema } from '../.test-build/user-panel.js';
import { DynamicUserPanel } from '../.test-build/DynamicUserPanel.js';

const schema = { id: 'review-root', type: 'Maipa.Stack', children: [
  { id: 'review-note', type: 'Maipa.Note', props: { text: 'Check the source before making changes.' } },
  { id: 'review-facts', type: 'Maipa.ClientFacts' },
] };

test('published screen bridge creates navigates updates and removes bounded work views', async () => {
  const model = new UserPanelModel('1');
  const kinds = await model.server.callTool('screens_list_kinds', {});
  assert.ok(JSON.stringify(kinds).includes('maipa-composition'));
  const created = await model.server.callTool('screens_create', { id: 'client-review', title: 'Client review',
    kind: 'maipa-composition', config: { schema } });
  assert.notEqual(created.isError, true);
  assert.equal(model.current.active, 'client-review');
  assert.deepEqual(model.current.views.find(view => view.id === 'client-review').schema, schema);
  const updated = await model.server.callTool('screens_update_content', { id: 'client-review', partial: {
    schema: { id: 'review-guide', type: 'Maipa.WorkspaceGuide' } } });
  assert.notEqual(updated.isError, true);
  assert.equal(model.current.views.find(view => view.id === 'client-review').schema.type, 'Maipa.WorkspaceGuide');
  await model.server.callTool('screens_navigate', { screen: 'client-facts' });
  assert.equal(model.current.active, 'client-facts');
  assert.notEqual((await model.server.callTool('screens_destroy', { id: 'client-review' })).isError, true);
  assert.equal(model.current.views.length, 2);
  const html = renderToStaticMarkup(React.createElement(DynamicUserPanel, { userId: '1', factsAttempt: 0 }));
  assert.match(html, /data-fancy-screens-screen/);
  assert.match(html, /aria-label="Work views"/);
  assert.match(html, /Workspace guide/);
  model.dispose();
});

test('schema validation rejects arbitrary code bindings unsafe props and unbounded documents', () => {
  assert.deepEqual(validateWorkSchema(schema), schema);
  for (const invalid of [
    { id: 'x', type: 'iframe', props: { src: 'https://foreign.invalid' } },
    { id: 'x', type: 'Maipa.ClientFacts', props: { user_id: '2' } },
    { id: 'x', type: 'Maipa.Note', props: { text: 'x', dangerouslySetInnerHTML: { __html: '<script>' } } },
    { id: 'x', type: 'Maipa.Note', props: { text: 'x', onClick: 'steal()' } },
    { id: 'x', type: 'Maipa.Note', props: { text: 'x'.repeat(2001) } },
    { id: 'x', type: 'Maipa.Stack', children: [{ id: 'x', type: 'Maipa.WorkspaceGuide' }] },
    { id: 'x', type: 'Maipa.Stack', children: [{ id: 'a', type: 'Maipa.ClientFacts' }, { id: 'b', type: 'Maipa.ClientFacts' }] },
    { type: 'Maipa.WorkspaceGuide' }, { id: 'x', type: 'Maipa.Stack', children: Array(17).fill(schema) },
  ]) assert.throws(() => validateWorkSchema(invalid));
});

test('invalid bridge changes preserve the current catalog and never gain another principals views', async () => {
  const first = new UserPanelModel('1');
  const other = new UserPanelModel('2');
  await first.server.callTool('screens_create', { id: 'private-review', kind: 'maipa-composition', config: { schema } });
  const previous = structuredClone(first.current);
  for (const [name, args] of [
    ['screens_update_content', { id: 'private-review', partial: { schema: { id: 'x', type: 'script' } } }],
    ['screens_update_content', { id: 'private-review', partial: { user_id: '2' } }],
    ['screens_create', { id: 'another', kind: 'arbitrary-code', config: { schema } }],
    ['screens_create', { id: '../escape', kind: 'maipa-composition', config: { schema } }],
    ['screens_destroy', { id: 'client-facts' }],
    ['screens_navigate', { screen: 'missing' }],
    ['screens_list', JSON.parse('{"__proto__":{}}')],
    ['screens_list', { constructor: 'foreign' }],
  ]) {
    assert.equal((await first.server.callTool(name, args)).isError, true);
    assert.deepEqual(first.current, previous);
  }
  assert.equal((await other.server.callTool('screens_navigate', { screen: 'private-review' })).isError, true);
  assert.equal(other.current.active, 'client-facts');
  assert.equal(other.current.views.length, 2);
  assert.equal((await other.server.callTool('agent_history', { agentId: 'foreign-principal' })).isError, true);
  assert.equal((await other.server.callTool('agent_undo', { agentId: 'foreign-principal' })).isError, true);
  assert.equal((await other.server.callTool('screens_list', { user_id: '1' })).isError, true);
  first.dispose();
  assert.equal(first.server.listTools().length, 0);
  await assert.rejects(first.server.callTool('screens_navigate', { screen: 'client-facts' }));
  assert.equal(other.current.views.length, 2);
  other.dispose();
});

test('accepted schema is copied and view quotas and reserved builtins remain enforced', async () => {
  const model = new UserPanelModel('1');
  const original = structuredClone(schema);
  await model.server.callTool('screens_create', { id: 'first', kind: 'maipa-composition', config: { schema: original } });
  original.children[0].props.text = 'mutated after validation';
  assert.equal(model.current.views.find(view => view.id === 'first').schema.children[0].props.text, schema.children[0].props.text);
  for (let index = 0; index < 5; index++) {
    assert.notEqual((await model.server.callTool('screens_create', { id: `view-${index}`, kind: 'maipa-composition', config: { schema } })).isError, true);
  }
  assert.equal(model.current.views.length, 8);
  const previous = structuredClone(model.current);
  assert.equal((await model.server.callTool('screens_create', { id: 'overflow', kind: 'maipa-composition', config: { schema } })).isError, true);
  assert.deepEqual(model.current, previous);
  model.dispose();
});
