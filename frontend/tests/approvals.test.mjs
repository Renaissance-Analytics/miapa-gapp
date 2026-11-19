import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { ChatMessage } from '@particle-academy/react-fancy';
import { approvalContent, approvalGroups, decisionMessage, approvalMessage, decideApproval, decideApprovalBatch, parseApprovalList, readApprovals } from '../.test-build/approval-client.js';

const id = n => `12345678-1234-4234-8234-${String(n).padStart(12, '0')}`;
const post = { id: id(1), operation: 'journal.post', summary: 'Post office purchase 42.17', payload: { book_id: id(2), journal_id: id(3), payload_hash: 'a'.repeat(64) },
  payload_hash: 'b'.repeat(64), batch_id: id(9), state: 'pending', detail: { date: '2026-03-05', description: 'Synthetic <b>office</b>', currency: 'USD', reference: 'r', current: true,
    lines: [{ account: '6200 Office expenses', debit: '42.1700', credit: '0.0000' }, { account: '1000 Checking', debit: '0.0000', credit: '42.1700' }] },
  result: null, error: null, created_at: '2026-11-18T00:00:00+00:00', decided_at: null };
const second = { ...post, id: id(4), payload_hash: 'c'.repeat(64) };
const single = { ...post, id: id(5), batch_id: null, operation: 'books.create', summary: 'Create USD books', payload: { entity_id: id(6), currency: 'USD', period_start: '2026-01-01', period_end: '2026-12-31' }, detail: { entity: 'Sample Co LLC' } };
const list = { actor_id: '1', pending: [post, second, single], recent: [] };
const token = { csrf_token: 'x'.repeat(64) };

test('approval list accepts only this actor and consistent pending and decided requests', async () => {
  assert.deepEqual(parseApprovalList(structuredClone(list), '1'), list);
  for (const broken of [{ ...list, actor_id: '2' }, { ...list, pending: [{ ...post, state: 'approved' }] }, { ...list, pending: [post, post] },
    { ...list, pending: [{ ...post, operation: 'books.delete' }] }, { ...list, pending: [{ ...post, payload_hash: 'short' }] },
    { ...list, recent: [post] }, { ...list, pending: [{ ...post, approver: '1' }] }])
    assert.throws(() => parseApprovalList(structuredClone(broken), '1'));
  const calls = [];
  assert.deepEqual(await readApprovals('1', new AbortController().signal, async (url, options) => { calls.push({ url, options }); return Response.json(list); }), list);
  assert.equal(calls[0].url, '/api/approvals/');
  assert.equal(calls[0].options.headers['X-Maipa-Agent-Actor'], '1');
});

test('one decision sends the reviewed hash and rejects a mismatched or undecided acknowledgment', async () => {
  const calls = [];
  const fetcher = reply => async (url, options) => { calls.push({ url, options }); return Response.json(url === '/api/session/csrf/' ? token : reply); };
  const signal = new AbortController().signal;
  const done = await decideApproval('1', post, 'approve', signal, fetcher({ ...post, state: 'approved', detail: null, result: { state: 'posted' }, decided_at: '2026-11-18T00:01:00+00:00' }));
  assert.equal(done.state, 'approved');
  assert.equal(calls[1].url, `/api/approvals/${id(1)}/decide/`);
  assert.deepEqual(JSON.parse(calls[1].options.body), { decision: 'approve', payload_hash: 'b'.repeat(64) });
  assert.equal((await decideApproval('1', post, 'approve', signal, fetcher({ ...post, state: 'failed', detail: null, error: 'ledger_review_conflict' }))).state, 'failed');
  for (const reply of [{ ...post }, { ...post, id: id(8), state: 'approved' }, { ...post, state: 'approved', payload_hash: 'd'.repeat(64) }])
    await assert.rejects(() => decideApproval('1', post, 'approve', signal, fetcher(reply)));
  await assert.rejects(() => decideApproval('1', post, 'reject', signal, fetcher({ ...post, state: 'approved' })));
  await assert.rejects(() => decideApproval('1', { ...post, state: 'approved' }, 'approve', signal, fetcher(post)));
});

test('a batch decision sends exactly the reviewed hashes and requires every item decided', async () => {
  const calls = [];
  const fetcher = reply => async (url, options) => { calls.push({ url, options }); return Response.json(url === '/api/session/csrf/' ? token : reply); };
  const signal = new AbortController().signal;
  const decided = [{ ...post, state: 'approved', detail: null }, { ...second, state: 'failed', detail: null, error: 'ledger_review_conflict' }];
  assert.deepEqual((await decideApprovalBatch('1', [post, second], 'approve', signal, fetcher({ batch_id: id(9), requests: decided }))).map(r => r.state), ['approved', 'failed']);
  assert.equal(calls[1].url, `/api/approvals/batch/${id(9)}/decide/`);
  assert.deepEqual(JSON.parse(calls[1].options.body), { decision: 'approve', payload_hashes: { [id(1)]: 'b'.repeat(64), [id(4)]: 'c'.repeat(64) } });
  await assert.rejects(() => decideApprovalBatch('1', [post, second], 'approve', signal, fetcher({ batch_id: id(9), requests: [decided[0]] })));
  await assert.rejects(() => decideApprovalBatch('1', [post, single], 'approve', signal, fetcher({ batch_id: id(9), requests: decided })));
});

test('cards group batches, show server-read entry lines and render through the Fancy approval card', () => {
  assert.deepEqual(approvalGroups(list.pending).map(g => g.map(r => r.id)), [[id(1), id(4)], [id(5)]]);
  const content = approvalContent(post);
  assert.match(content, /\| 6200 Office expenses \| 42\.1700 \| 0\.0000 \|/);
  assert.doesNotMatch(content, /<b>/);
  assert.match(approvalContent({ ...post, detail: { ...post.detail, current: false } }), /Approving will fail/);
  assert.match(approvalContent(single), /Sample Co LLC · USD · 2026-01-01 to 2026-12-31/);
  const message = approvalMessage(post);
  assert.deepEqual(message.proposal, { id: id(1), title: 'Post a journal entry', state: 'pending', acceptLabel: 'Approve', rejectLabel: 'Reject' });
  const html = renderToStaticMarkup(React.createElement(ChatMessage, { message, onProposalAccept() {}, onProposalReject() {} }));
  assert.match(html, new RegExp(`data-chat-proposal="${id(1)}"`));
  assert.match(html, />Approve</);
  assert.match(html, />Reject</);
  const decided = renderToStaticMarkup(React.createElement(ChatMessage, { message: approvalMessage({ ...post, state: 'approved' }) }));
  assert.doesNotMatch(decided, />Approve</);
});

test('the note sent after a decision states exact outcomes and is empty when nothing was decided', () => {
  const text = decisionMessage([{ ...post, state: 'approved' }, { ...second, state: 'failed', summary: 'Post rent <b>900</b>' }, { ...single, state: 'rejected' }]);
  assert.equal(text, 'I approved and Maipa completed: Post office purchase 42.17. I rejected (nothing changed): Create USD books. '
    + 'I approved but these could not be done and need to be prepared again: Post rent  b 900 /b . Please continue with what is left.');
  assert.equal(decisionMessage([]), '');
  assert.equal(decisionMessage([post]), '');
});
