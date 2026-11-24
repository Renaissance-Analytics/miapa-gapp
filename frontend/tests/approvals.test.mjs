import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { ChatMessage } from '@particle-academy/react-fancy';
import { approvalContent, approvalGroups, decisionMessage, approvalMessage, decideApproval, decideApprovalBatch, parseApprovalList, readApprovals, uploadAttachment, uploadWorkbook, workbookProblem } from '../.test-build/approval-client.js';

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

test('an attached file is uploaded for this actor and only its exact acknowledgment is accepted', async () => {
  const content = 'date,description,amount\n2026-03-01,Synthetic,1\n';
  const saved = { id: id(7), name: 'march.csv', size: content.length, sha256: 'e'.repeat(64), lines: 2, created_at: '2026-11-19T00:00:00+00:00' };
  const calls = [];
  const fetcher = (reply, status = 201) => async (url, options) => { calls.push({ url, options }); return Response.json(url === '/api/session/csrf/' ? token : reply, { status: url === '/api/session/csrf/' ? 200 : status }); };
  const signal = new AbortController().signal;
  assert.deepEqual(await uploadAttachment('1', 'march.csv', content, signal, fetcher(saved)), saved);
  assert.equal(calls[1].url, '/api/agent/attachments/');
  assert.deepEqual(JSON.parse(calls[1].options.body), { name: 'march.csv', content });
  assert.equal(calls[1].options.headers['X-Maipa-Agent-Actor'], '1');
  for (const reply of [{ ...saved, name: 'other.csv' }, { ...saved, size: 1 }, { ...saved, content }])
    await assert.rejects(() => uploadAttachment('1', 'march.csv', content, signal, fetcher(reply)));
  await assert.rejects(() => uploadAttachment('1', 'march.csv', content, signal, fetcher({ error: 'x' }, 400)));
});

test('a reversal card shows the opposite lines and says the original entry is kept', () => {
  const reverse = { ...post, operation: 'journal.reverse', summary: 'Reverse the rent entry', payload: { book_id: id(2), journal_id: id(3), date: '2026-03-31' },
    detail: { date: '2026-03-31', description: 'Reversal of: Synthetic rent', currency: 'USD', original_date: '2026-03-03', current: true,
      lines: [{ account: '6200 Office expenses', debit: '0.0000', credit: '900.0000' }, { account: '1000 Checking', debit: '900.0000', credit: '0.0000' }] } };
  assert.deepEqual(parseApprovalList({ actor_id: '1', pending: [reverse], recent: [] }, '1').pending[0].operation, 'journal.reverse');
  const content = approvalContent(reverse);
  assert.match(content, /\| 6200 Office expenses \| 0\.0000 \| 900\.0000 \|/);
  assert.match(content, /exact opposite of the entry dated 2026-03-03\. The original entry stays in your books\./);
  assert.equal(approvalMessage(reverse).proposal.title, 'Reverse a posted entry');
});

test('period cards say exactly what closing or reopening does', () => {
  const close = { ...single, operation: 'period.close', summary: 'Close March', payload: { book_id: id(2), through: '2026-03-31' }, detail: { books: 'Sample Co LLC', closed_through: null } };
  assert.match(approvalContent(close), /Close Sample Co LLC books through 2026-03-31\. Entries on or before that date can no longer be added, posted or reversed into\. Posted history is not changed\./);
  assert.equal(approvalMessage(close).proposal.title, 'Close the books');
  const reopen = { ...close, operation: 'period.reopen', payload: { book_id: id(2), through: null }, detail: { books: 'Sample Co LLC', closed_through: '2026-03-31' } };
  assert.match(approvalContent(reopen), /Reopen Sample Co LLC books fully \(currently closed through 2026-03-31\)\./);
  assert.match(approvalContent({ ...reopen, payload: { book_id: id(2), through: '2026-02-28' } }), /Move the close of Sample Co LLC books back to 2026-02-28 \(currently 2026-03-31\)\./);
  assert.equal(parseApprovalList({ actor_id: '1', pending: [close, { ...reopen, id: id(7) }], recent: [] }, '1').pending.length, 2);
});

test('an opening balance card shows every line, the totals and what goes to opening balance equity', () => {
  const opening = { ...single, operation: 'opening.balance', summary: 'Record opening balances',
    payload: { book_id: id(2), balances: [{ account_id: id(3), debit: '5000', credit: '0' }, { account_id: id(4), debit: '0', credit: '700.25' }], control: { debit: '5000', credit: '700.25' } },
    detail: { books: 'Sample Co LLC', currency: 'USD', date: '2026-01-01', current: true, total_debit: '5000.0000', total_credit: '5000.0000',
      control_debit: '5000.0000', control_credit: '700.2500',
      lines: [{ account: '1000 Checking', debit: '5000.0000', credit: '0.0000', offset: false }, { account: '2000 Accounts payable', debit: '0.0000', credit: '700.2500', offset: false },
        { account: '3900 Opening balance equity', debit: '0.0000', credit: '4299.7500', offset: true }] } };
  assert.equal(parseApprovalList({ actor_id: '1', pending: [opening], recent: [] }, '1').pending[0].operation, 'opening.balance');
  const content = approvalContent(opening);
  assert.match(content, /Sample Co LLC books · starting 2026-01-01 · USD/);
  assert.match(content, /\| 3900 Opening balance equity \| 0\.0000 \| 4299\.7500 \|/);
  assert.match(content, /\| \*\*Total\*\* \| 5000\.0000 \| 5000\.0000 \|/);
  assert.match(content, /Your figures total 5000\.0000 in debits and 700\.2500 in credits\. The difference goes to 3900 Opening balance equity so the entry balances\./);
  assert.equal(approvalMessage(opening).proposal.title, 'Record opening balances');
  const balanced = { ...opening, detail: { ...opening.detail, control_credit: '5000.0000', lines: opening.detail.lines.slice(0, 2) } };
  assert.match(approvalContent(balanced), /They balance, so nothing goes to opening balance equity\./);
  assert.match(approvalContent({ ...opening, detail: { books: 'Sample Co LLC', currency: 'USD', current: false } }), /can no longer be recorded as asked\. Approving will fail/);
});

test('a bank or cash account card names the account and says no amounts change', () => {
  const mark = { ...single, operation: 'account.cash', summary: 'Mark Savings as a bank account', payload: { book_id: id(2), account_id: id(3), cash: true },
    detail: { books: 'Sample Co LLC', account: '1010 Savings', cash: false } };
  assert.equal(parseApprovalList({ actor_id: '1', pending: [mark], recent: [] }, '1').pending[0].operation, 'account.cash');
  assert.match(approvalContent(mark), /1010 Savings in Sample Co LLC books will count as a bank account or cash on hand\. No amounts change\./);
  assert.match(approvalContent({ ...mark, payload: { ...mark.payload, cash: false } }), /1010 Savings in Sample Co LLC books will no longer count as a bank account or cash on hand\./);
  assert.match(approvalContent({ ...mark, detail: null }), /can no longer be found\. Approving will fail/);
  assert.equal(approvalMessage(mark).proposal.title, 'Mark a bank or cash account');
});

test('a statement import card names the bank account the statement is from when one is given', () => {
  const load = { ...single, operation: 'statement.import', summary: 'Import the March statement',
    payload: { book_id: id(2), filename: 'march.csv', account_id: id(3), content_bytes: 120, content_sha256: 'c'.repeat(64), content_preview: 'date,description,amount' },
    detail: { books: 'Sample Co LLC', currency: 'USD', account: '1000 Checking' } };
  assert.equal(parseApprovalList({ actor_id: '1', pending: [load], recent: [] }, '1').pending[0].operation, 'statement.import');
  assert.match(approvalContent(load), /march\.csv · 120 bytes into Sample Co LLC books, as a statement of 1000 Checking\. Rows become pending transactions; no entries are posted\./);
  assert.match(approvalContent({ ...load, detail: { ...load.detail, account: null } }), /120 bytes into Sample Co LLC books\. Rows become pending/);
});

test('an Excel statement is sent as its exact bytes and a refusal says what to fix', async () => {
  const bytes = new Uint8Array([80, 75, 3, 4, 0, 255, 128, 10]);
  const saved = { id: id(7), name: 'march.xlsx', size: bytes.length, sha256: 'e'.repeat(64), lines: 3, created_at: '2026-11-19T00:00:00+00:00' };
  const calls = [];
  const fetcher = (reply, status = 201) => async (url, options) => { calls.push({ url, options }); return Response.json(url === '/api/session/csrf/' ? token : reply, { status: url === '/api/session/csrf/' ? 200 : status }); };
  const signal = new AbortController().signal;
  assert.deepEqual(await uploadWorkbook('1', 'march.xlsx', bytes, signal, fetcher(saved)), saved);
  assert.deepEqual(JSON.parse(calls[1].options.body), { name: 'march.xlsx', content_base64: 'UEsDBAD/gAo=' });
  assert.equal(calls[1].options.headers['X-Maipa-Agent-Actor'], '1');
  for (const reply of [{ ...saved, name: 'other.xlsx' }, { ...saved, size: 1 }, { ...saved, rows: [] }])
    await assert.rejects(() => uploadWorkbook('1', 'march.xlsx', bytes, signal, fetcher(reply)));
  const sent = calls.length;
  await assert.rejects(() => uploadWorkbook('1', 'march.xlsx', new Uint8Array(0), signal, fetcher(saved)));
  await assert.rejects(() => uploadWorkbook('1', 'march.xlsx', new Uint8Array(524_289), signal, fetcher(saved)));
  assert.equal(calls.length, sent);
  await assert.rejects(() => uploadWorkbook('1', 'march.xlsx', bytes, signal, fetcher({ error: { code: 'import_row_7_invalid' } }, 400)), /Row 7 of that spreadsheet is not a valid statement row/);
  await assert.rejects(() => uploadWorkbook('1', 'march.xlsx', bytes, signal, fetcher({ error: { code: 'import_workbook_sheets' } }, 400)), /more than one sheet/);
  assert.match(workbookProblem('import_header_invalid'), /headings date, description, amount/);
  assert.match(workbookProblem('import_row_count_invalid'), /no statement rows, or more than 1,000/);
  for (const code of ['import_workbook_invalid', 'import_row_x_invalid', undefined, { code: 1 }]) assert.match(workbookProblem(code), /could not read that file/);
  const large = new Uint8Array(100_000).fill(65);
  await uploadWorkbook('1', 'big.xlsx', large, signal, fetcher({ ...saved, name: 'big.xlsx', size: large.length }));
  assert.equal(JSON.parse(calls.at(-1).options.body).content_base64.length, Math.ceil(large.length / 3) * 4);
});

test('a long summary is shown whole up to the server limit, with markup characters made inert', () => {
  const long = 'Record opening balances: ' + 'Dr 1500 Equipment 1,200.00; '.repeat(13) + 'the 900.00 difference is credited to 3900 Opening balance equity.';
  assert.equal(long.length > 400 && long.length <= 500, true);
  const content = approvalContent({ ...single, summary: long });
  assert.equal(content.split('\n')[0], long);
  assert.match(content, /credited to 3900 Opening balance equity\.$/m);
  assert.equal(approvalContent({ ...single, summary: 'x'.repeat(600) }).split('\n')[0].length, 500);
  assert.equal(approvalContent({ ...single, summary: 'Pay <b>*now*</b> | [link]' }).split('\n')[0], 'Pay  b  now  /b     link ');
  assert.equal(approvalContent({ ...single, detail: { entity: 'E'.repeat(300) } }).split('\n')[2].startsWith('E'.repeat(200) + ' ·'), true);
});

test('a reconciled statement card shows the rows, both balances and a check-the-PDF note', () => {
  const load = { ...single, operation: 'statement.import', summary: 'Import the June statement',
    payload: { book_id: id(2), filename: 'june.pdf', account_id: id(3), opening_balance: '3799.55', closing_balance: '5287.05',
      content_bytes: 96, content_sha256: 'c'.repeat(64), content_preview: 'date,description,amount' },
    detail: { books: 'Sample Co LLC', currency: 'USD', account: '1000 Checking', opening_balance: '3799.5500', closing_balance: '5287.0500', net: '1487.5000', row_count: 3,
      rows: [{ date: '2026-06-12', description: 'Epsilon LLC | payment', amount: '1500.0000' }, { date: '2026-06-15', description: 'Card fee', amount: '-12.5000' }] } };
  assert.equal(parseApprovalList({ actor_id: '1', pending: [load], recent: [] }, '1').pending[0].operation, 'statement.import');
  const content = approvalContent(load);
  assert.match(content, /\| 2026-06-12 \| Epsilon LLC   payment \| 1500\.0000 \|/);
  assert.match(content, /\| 2026-06-15 \| Card fee \| -12\.5000 \|/);
  assert.match(content, /…and 1 more row\./);
  assert.match(content, /Opening balance 3799\.5500 \+ rows 1487\.5000 = closing balance 5287\.0500 \(USD\)\. These 3 rows add up exactly between the statement's two balances\./);
  assert.match(content, /Maipa read these rows from the PDF\. Check them against your statement before approving\./);
  const sheet = approvalContent({ ...load, payload: { ...load.payload, filename: 'june.csv' }, detail: { ...load.detail, row_count: 2 } });
  assert.doesNotMatch(sheet, /read these rows from the PDF/); assert.doesNotMatch(sheet, /more row/); assert.match(sheet, /These 2 rows add up exactly/);
  assert.doesNotMatch(approvalContent({ ...load, detail: { books: 'Sample Co LLC', currency: 'USD', account: null } }), /Opening balance/);
});

test('a PDF statement is sent as bytes up to its own limit and its refusals say what to do', async () => {
  const bytes = new Uint8Array([37, 80, 68, 70]);
  const saved = { id: id(7), name: 'june.pdf', size: bytes.length, sha256: 'e'.repeat(64), lines: 9, created_at: '2026-11-19T00:00:00+00:00' };
  const calls = [];
  const fetcher = (reply, status = 201) => async (url, options) => { calls.push({ url, options }); return Response.json(url === '/api/session/csrf/' ? token : reply, { status: url === '/api/session/csrf/' ? 200 : status }); };
  const signal = new AbortController().signal;
  assert.deepEqual(await uploadWorkbook('1', 'june.pdf', bytes, signal, fetcher(saved)), saved);
  assert.deepEqual(JSON.parse(calls[1].options.body), { name: 'june.pdf', content_base64: 'JVBERg==' });
  const big = new Uint8Array(600_000).fill(65);
  await uploadWorkbook('1', 'june.pdf', big, signal, fetcher({ ...saved, size: big.length }));
  const sent = calls.length;
  await assert.rejects(() => uploadWorkbook('1', 'june.pdf', new Uint8Array(716_801), signal, fetcher(saved)));
  await assert.rejects(() => uploadWorkbook('1', 'june.xlsx', big, signal, fetcher({ ...saved, name: 'june.xlsx', size: big.length })));
  assert.equal(calls.length, sent);
  await assert.rejects(() => uploadWorkbook('1', 'scan.pdf', bytes, signal, fetcher({ error: { code: 'import_document_no_text' } }, 400)), /no readable text; it looks like a scanned image/);
  assert.match(workbookProblem('import_document_invalid'), /could not read that PDF/);
  assert.match(workbookProblem('unknown'), /could not read that file/);
});

test('a match card shows the statement line, the entry and its lines, and says nothing is booked twice', () => {
  const match = { ...single, operation: 'statement.match', summary: 'Match the savings line to the transfer', payload: { book_id: id(2), row_id: id(3), journal_id: id(4) },
    detail: { books: 'Sample Co LLC', currency: 'USD', row: { date: '2026-03-03', description: 'Transfer from checking', amount: '500.0000', account: '1010 Savings' },
      entry: { sequence: 7, date: '2026-03-02', description: 'Transfer to savings', state: 'draft' },
      lines: [{ account: '1010 Savings', debit: '500.0000', credit: '0.0000' }, { account: '1000 Checking', debit: '0.0000', credit: '500.0000' }] } };
  assert.equal(parseApprovalList({ actor_id: '1', pending: [match], recent: [] }, '1').pending[0].operation, 'statement.match');
  const content = approvalContent(match);
  assert.match(content, /Statement line: 2026-03-03 · Transfer from checking · 500\.0000 USD on 1010 Savings/);
  assert.match(content, /Entry #7 dated 2026-03-02 · Transfer to savings · awaiting your review/);
  assert.match(content, /\| 1000 Checking \| 0\.0000 \| 500\.0000 \|/);
  assert.match(content, /already records the movement, so the line is settled without a second entry\. No amounts change\./);
  assert.match(approvalContent({ ...match, detail: { ...match.detail, entry: { ...match.detail.entry, state: 'posted' } } }), /Transfer to savings · posted/);
  assert.match(approvalContent({ ...match, detail: null }), /can no longer be found\. Approving will fail/);
  assert.equal(approvalMessage(match).proposal.title, 'Match a statement line to an entry');
});

test('a duplicate card shows both lines side by side and how to refuse a false duplicate', () => {
  const repeat = { ...single, operation: 'statement.duplicate', summary: 'Mark the repeated client payment', payload: { book_id: id(2), row_id: id(3), of_row_id: id(4) },
    detail: { books: 'Sample Co LLC', currency: 'USD', account: '1000 Checking',
      row: { statement: 'late-march.csv', date: '2026-03-30', description: 'CLIENT PAYMENT', amount: '1250.5000' },
      of: { statement: 'march.csv', date: '2026-03-30', description: 'Client payment', amount: '1250.5000' } } };
  assert.equal(parseApprovalList({ actor_id: '1', pending: [repeat], recent: [] }, '1').pending[0].operation, 'statement.duplicate');
  const content = approvalContent(repeat);
  assert.match(content, /1000 Checking · USD/);
  assert.match(content, /\| This line \| late-march\.csv \| 2026-03-30 \| CLIENT PAYMENT \| 1250\.5000 \|/);
  assert.match(content, /\| Repeats \| march\.csv \| 2026-03-30 \| Client payment \| 1250\.5000 \|/);
  assert.match(content, /settled as a repeat and gets no entry, so the movement is counted once\. If these were two separate transactions, reject this\./);
  assert.match(approvalContent({ ...repeat, detail: null }), /can no longer be found\. Approving will fail/);
  assert.equal(approvalMessage(repeat).proposal.title, 'Mark a repeated statement line');
});

test('a statement account card names the statement, its lines and the account, and warns when it is stale', () => {
  const tie = { ...single, operation: 'statement.account', summary: 'Tie april.csv to Checking', payload: { book_id: id(2), import_id: id(3), account_id: id(4) },
    detail: { books: 'Sample Co LLC', statement: 'april.csv', row_count: 2, account: '1000 Checking', current: true } };
  assert.equal(parseApprovalList({ actor_id: '1', pending: [tie], recent: [] }, '1').pending[0].operation, 'statement.account');
  const content = approvalContent(tie);
  assert.match(content, /april\.csv \(2 lines\) in Sample Co LLC books will be recorded as a statement of 1000 Checking\. Entries already made from its lines agree with that account\. No entries or amounts change\./);
  assert.doesNotMatch(content, /already tied/);
  assert.match(approvalContent({ ...tie, detail: { ...tie.detail, row_count: 1, current: false } }), /\(1 line\).*already tied to an account\. Approving will fail/);
  assert.match(approvalContent({ ...tie, detail: null }), /can no longer be found\. Approving will fail/);
  assert.equal(approvalMessage(tie).proposal.title, 'Tie a statement to its bank account');
});

test('balance and reconciliation cards show the exact figures and say when approving will fail', () => {
  const balances = { ...single, operation: 'statement.balances', summary: 'Add the March balances', payload: { book_id: id(2), import_id: id(3), opening_balance: '1000', closing_balance: '1350.5' },
    detail: { books: 'Sample Co LLC', currency: 'USD', statement: 'march.csv', account: '1000 Checking', row_count: 2, net: '350.5000', opening_balance: '1000.0000', closing_balance: '1350.5000', current: true } };
  const record = { ...single, operation: 'reconciliation.record', summary: 'Record the March reconciliation', payload: { book_id: id(2), import_id: id(3) },
    detail: { books: 'Sample Co LLC', currency: 'USD', statement: 'march.csv', account: '1000 Checking', as_of: '2026-03-05', closing_balance: '1350.5000', book_balance: '1360.5000',
      unrecorded_total: '-10.0000', outstanding_total: '0.0000', difference: '0.0000', reconciled: true } };
  assert.deepEqual(parseApprovalList({ actor_id: '1', pending: [balances, { ...record, id: id(7) }], recent: [] }, '1').pending.map(p => p.operation), ['statement.balances', 'reconciliation.record']);
  const first = approvalContent(balances);
  assert.match(first, /march\.csv · 1000 Checking · Sample Co LLC books/);
  assert.match(first, /Opening balance 1000\.0000 \+ 2 lines 350\.5000 = closing balance 1350\.5000 \(USD\)\. These are recorded as the balances printed on the statement\. No entries or amounts change\./);
  assert.match(approvalContent({ ...balances, detail: { ...balances.detail, row_count: 1, account: null, current: false } }), /no bank account yet.*\+ 1 line .*already has balances\. Approving will fail/s);
  const second = approvalContent(record);
  assert.match(second, /march\.csv · 1000 Checking · as of 2026-03-05/);
  assert.match(second, /\| Bank closing balance \| 1350\.5000 \|/); assert.match(second, /\| Books balance \| 1360\.5000 \|/);
  assert.match(second, /\| Statement lines not yet posted \| -10\.0000 \|/); assert.match(second, /\| Difference \| 0\.0000 \|/);
  assert.match(second, /agrees with the books\. Approving records that you reviewed it\. No entries or amounts change\./);
  assert.match(approvalContent({ ...record, detail: { ...record.detail, difference: '-49.0000', reconciled: false } }), /no longer agrees with the books\. Approving will fail/);
  for (const item of [balances, record]) assert.match(approvalContent({ ...item, detail: null }), /can no longer be found\. Approving will fail/);
  assert.deepEqual([approvalMessage(balances).proposal.title, approvalMessage(record).proposal.title], ["Add a statement's balances", 'Record a bank reconciliation']);
});

test('a discard card shows the draft lines and says nothing was posted', () => {
  const discard = { ...post, operation: 'journal.discard', summary: 'Discard the wrong rent draft', payload: { book_id: id(2), journal_id: id(3) },
    detail: { date: '2026-03-05', description: 'Synthetic <b>rent</b>', currency: 'USD', current: true,
      lines: [{ account: '6100 Bank fees', debit: '900.0000', credit: '0.0000' }, { account: '1000 Checking', debit: '0.0000', credit: '900.0000' }] } };
  assert.equal(parseApprovalList({ actor_id: '1', pending: [discard], recent: [] }, '1').pending[0].operation, 'journal.discard');
  assert.equal(approvalMessage(discard).proposal.title, 'Discard a draft entry');
  const content = approvalContent(discard);
  assert.match(content, /\| 6100 Bank fees \| 900\.0000 \| 0\.0000 \|/); assert.equal(content.includes('2026-03-05 · Synthetic  b rent /b  · USD'), true); assert.doesNotMatch(content, /<b>/);
  assert.match(content, /This draft was never posted, so no amounts change\. If it came from a statement line, that line becomes pending again\./);
  assert.doesNotMatch(content, /exact opposite/);
  const gone = approvalContent({ ...discard, detail: { ...discard.detail, current: false } });
  assert.match(gone, /no longer a draft, so there is nothing to discard\. Approving will fail\./); assert.doesNotMatch(gone, /never posted/);
  assert.throws(() => parseApprovalList({ actor_id: '1', pending: [{ ...discard, operation: 'journal.delete' }], recent: [] }, '1'));
});

test('a recurring entry card shows the lines, the day and that each month still needs approval', () => {
  const create = { ...post, operation: 'schedule.create', summary: 'Rent every month', payload: { book_id: id(2), description: 'Office rent', lines: [], day: 31, starts: '2026-01-15', ends: '2026-06-30' },
    detail: { books: 'Synthetic <b>Co</b>', currency: 'USD', description: 'Office rent', day: 31, starts: '2026-01-15', ends: '2026-06-30', total: '900.0000',
      lines: [{ account: '6200 Office expenses', debit: '900.0000', credit: '0.0000' }, { account: '1000 Checking', debit: '0.0000', credit: '900.0000' }] } };
  assert.equal(parseApprovalList({ actor_id: '1', pending: [create], recent: [] }, '1').pending[0].operation, 'schedule.create');
  assert.equal(approvalMessage(create).proposal.title, 'Set up a recurring entry');
  const content = approvalContent(create);
  assert.match(content, /\| 6200 Office expenses \| 900\.0000 \| 0\.0000 \|/);
  assert.equal(content.includes('Office rent · 900.0000 USD · on day 31 of each month (the last day in shorter months) from 2026-01-15 to 2026-06-30'), true);
  assert.match(content, /Nothing posts by itself: each month Maipa drafts the entry and asks you to approve posting it\./);
  assert.doesNotMatch(content, /<b>/);
  const open = approvalContent({ ...create, detail: { ...create.detail, ends: null } });
  assert.equal(open.includes('from 2026-01-15 until the end of the books\' period'), true);
  const stop = { ...post, operation: 'schedule.stop', summary: 'Stop rent', payload: { book_id: id(2), schedule_id: id(4) },
    detail: { books: 'Synthetic Co', description: 'Office rent', day: 31, starts: '2026-01-15', ends: null, active: true } };
  assert.equal(approvalMessage(stop).proposal.title, 'Stop a recurring entry');
  assert.match(approvalContent(stop), /Stop drafting Office rent on day 31 of each month\. Drafts and posted entries it already made are not changed\./);
  assert.match(approvalContent({ ...stop, detail: { ...stop.detail, active: false } }), /already stopped\. Approving will fail\./);
  assert.match(approvalContent({ ...create, detail: null }), /can no longer be set up as asked\. Approving will fail/);
});

test('an accrual card shows the lines and both dates', () => {
  const accrual = { ...post, operation: 'accrual.post', summary: 'Accrue March utilities', payload: { book_id: id(2), date: '2026-03-31', description: 'Utilities', lines: [], reverse_on: '2026-04-01' },
    detail: { books: 'Synthetic Co', currency: 'USD', description: 'March <i>utilities</i>', date: '2026-03-31', reverse_on: '2026-04-01', total: '1200.0000',
      lines: [{ account: '6200 Office expenses', debit: '1200.0000', credit: '0.0000' }, { account: '2000 Accounts payable', debit: '0.0000', credit: '1200.0000' }] } };
  assert.equal(parseApprovalList({ actor_id: '1', pending: [accrual], recent: [] }, '1').pending[0].operation, 'accrual.post');
  assert.equal(approvalMessage(accrual).proposal.title, 'Post an accrual and its reversal');
  const content = approvalContent(accrual);
  assert.match(content, /\| 2000 Accounts payable \| 0\.0000 \| 1200\.0000 \|/); assert.doesNotMatch(content, /<i>/);
  assert.match(content, /This posts the entry on 2026-03-31 and its exact opposite on 2026-04-01, so it counts only in the period it belongs to\./);
  assert.match(approvalContent({ ...accrual, detail: null }), /can no longer be posted as asked\. Approving will fail/);
});

test('a scheduled workflow card says what starts, when, and that it runs by itself', () => {
  const detail = { workflow: 'Morning <b>summary</b>', version: 2, every: 'day', hour: 9, weekday: null, day: null, input: { name: 'Mon<i>day', count: 3 }, next: '2026-03-10T09:00:00+00:00', current: true };
  const scheduled = { ...post, operation: 'workflow.schedule', summary: 'Run the summary daily', payload: { draft_id: id(2), definition_revision: 2, every: 'day', hour: 9 }, detail };
  assert.equal(parseApprovalList({ actor_id: '1', pending: [scheduled], recent: [] }, '1').pending[0].operation, 'workflow.schedule');
  assert.equal(approvalMessage(scheduled).proposal.title, 'Run a workflow on a schedule');
  const content = approvalContent(scheduled);
  assert.match(content, /Start Morning .*summary.* \(version 2\) every day at 09:00 UTC\./); assert.doesNotMatch(content, /<b>/);
  assert.match(content, /First run: 2026-03-10 09:00 UTC\./);
  assert.match(content, /Each run starts with this input:\n\n\| Name \| Value \|\n\|---\|---\|\n\| name \| Mon i day \|\n\| count \| 3 \|/); assert.doesNotMatch(content, /<i>/);
  assert.match(approvalContent({ ...scheduled, detail: { ...detail, input: {} } }), /Each run starts with no input\./);
  assert.match(content, /starts each run by itself while it is running, once per time\..*keeps running this version.*stops if your access is removed\. Workflows cannot change your books\./);
  assert.match(approvalContent({ ...scheduled, detail: { ...detail, every: 'week', hour: 6, weekday: 0 } }), /every Monday at 06:00 UTC\./);
  assert.match(approvalContent({ ...scheduled, detail: { ...detail, every: 'month', hour: 0, day: 31 } }), /on day 31 of each month \(the last day in shorter months\) at 00:00 UTC\./);
  assert.match(approvalContent({ ...scheduled, detail: null }), /can no longer be scheduled as asked\. Approving will fail/);
  const stop = { ...post, operation: 'workflow.unschedule', summary: 'Stop the daily run', payload: { schedule_id: id(3) }, detail: { ...detail, next: undefined } };
  assert.equal(approvalMessage(stop).proposal.title, 'Stop a scheduled workflow');
  assert.match(approvalContent(stop), /Stop starting Morning .*summary.* every day at 09:00 UTC\. Runs already started are not changed\./);
  assert.match(approvalContent({ ...stop, detail: { ...detail, current: false } }), /is already stopped\. Approving will fail\./);
  assert.match(approvalContent({ ...stop, detail: null }), /could not be found\. Approving will fail\./);
});
