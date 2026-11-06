import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { AgentSurface } from '../.test-build/AgentSurface.js';
import { compositionSchema } from '../.test-build/agent-chat.js';

test('fine-grained Fancy heading and callout render inert commentary with stable handles', () => {
  const schema = compositionSchema({ id: 'overview', type: 'Maipa.Stack', children: [
    { id: 'overview-title', type: 'Maipa.Heading', props: { text: 'Review <script>unsafe</script>' } },
    { id: 'guidance', type: 'Maipa.Callout', props: { text: 'Compare the **current** records.', tone: 'attention' } },
  ] });
  const html = renderToStaticMarkup(React.createElement(AgentSurface, { userId: '1', ui: { composition: { title: 'Overview', schema } } }));
  assert.match(html, /data-react-fancy-heading/); assert.match(html, /data-react-fancy-callout/);
  assert.match(html, /&lt;script&gt;unsafe&lt;\/script&gt;/); assert.doesNotMatch(html, /<script/);
  // Fancy deliberately leaves HTML inert during server rendering; browser parity is checked natively.
  assert.match(html, /&lt;strong&gt;current&lt;\/strong&gt;/); assert.match(html, /Maipa · commentary/);
});

test('client-table definitions contain only approved ordered columns and no record or executable bindings', () => {
  const original = { id: 'current-clients', type: 'Maipa.ClientTable', props: { columns: ['entity_count', 'name'] } };
  const accepted = compositionSchema(original);
  original.props.columns.reverse(); assert.deepEqual(accepted.props.columns, ['entity_count', 'name']);
  for (const props of [
    { columns: ['name'], rows: [{ name: 'Untrusted snapshot' }] },
    { columns: ['name'], user_id: '2' }, { columns: ['name'], url: '/foreign' },
    { columns: ['name'], onClick: 'execute()' }, { columns: ['name', 'name'] },
    { columns: ['private_balance'] }, { columns: [] },
  ]) assert.throws(() => compositionSchema({ ...original, props }));
  for (const props of [{ text: 'x', tone: 'success' }, { text: 'x\u0000', tone: 'info' }, { text: 'x', tone: 'info', dismissible: true }])
    assert.throws(() => compositionSchema({ id: 'bad', type: 'Maipa.Callout', props }));
  assert.throws(() => compositionSchema({ id: 'bad', type: 'Maipa.Heading', props: { text: 'x'.repeat(121) } }));
});
