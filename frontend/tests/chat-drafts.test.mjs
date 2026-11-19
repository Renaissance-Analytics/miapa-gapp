import assert from 'node:assert/strict';
import test from 'node:test';
import { ChatDrafts, attachmentNote, emptyDraft, isWorkbook, sendProblem, sendableAttachment } from '../.test-build/chat-drafts.js';

test('complete drafts and attachment references restore under only their original account', () => {
  const drafts = new ChatDrafts();
  const value = { ...emptyDraft(), text: 'Unsent Ω', pills: [{ kind: 'text', id: 'p1', text: 'held text' }],
    attachments: [{ id: 'f1', name: 'workpaper.pdf', type: 'application/pdf', bytes: 12, ref: 'owned-file' }] };
  drafts.write('first', value); drafts.write('second', { ...emptyDraft(), text: 'Other account' });
  assert.deepEqual(drafts.read('first'), value); assert.equal(drafts.read('second').text, 'Other account');
  assert.deepEqual(drafts.read('unselected'), emptyDraft());
});
test('draft snapshots do not share mutable arrays or data with callers', () => {
  const drafts = new ChatDrafts(), value = { ...emptyDraft(), text: 'Keep me' };
  drafts.write('a', value); value.text = 'Changed'; const read = drafts.read('a'); read.attachments.push({ id: 'foreign' });
  assert.equal(drafts.read('a').text, 'Keep me'); assert.deepEqual(drafts.read('a').attachments, []);
});
test('retry identities remain account scoped and stable for the same failed payload', () => {
  const drafts = new ChatDrafts();
  assert.equal(drafts.request('a', 'Retry', () => 'id-a'), 'id-a');
  assert.equal(drafts.request('a', 'Retry', () => 'replacement'), 'id-a');
  assert.equal(drafts.request('b', 'Retry', () => 'id-b'), 'id-b');
  assert.equal(drafts.request('a', 'Different message', () => 'id-new'), 'id-new');
});
test('acknowledgment retires only its matching request and never clears a newer draft', () => {
  const drafts = new ChatDrafts(); drafts.request('a', 'Sent', () => 'old');
  drafts.write('a', { ...emptyDraft(), text: 'New typing' }); drafts.acknowledge('a', 'old');
  assert.equal(drafts.read('a').text, 'New typing'); assert.equal(drafts.request('a', 'Sent', () => 'new'), 'new');
  drafts.acknowledge('a', 'old'); assert.equal(drafts.request('a', 'Sent', () => 'wrong'), 'new');
});
test('unsupported attachments and over-limit messages are refused without rewriting drafts', () => {
  assert.match(sendProblem('Keep my attachment', [{ id: 'file' }]), /Remove them/);
  assert.match(sendProblem('a'.repeat(8001), []), /8,000/);
  assert.match(sendProblem('   ', []), /Write/);
  assert.equal(sendProblem('a'.repeat(8000), []), null); assert.equal(sendProblem('Valid Ω', []), null);
});

test('only picked CSV files within the size limit can be sent, and the note names each stored file', () => {
  const file = { text: async () => 'date,description,amount' };
  const csv = { id: 'a', name: 'march.csv', bytes: 100, file };
  assert.equal(sendProblem('Import this', [csv]), null);
  assert.equal(sendableAttachment(csv), true);
  for (const bad of [{ ...csv, name: 'notes.txt' }, { ...csv, bytes: 524289 }, { ...csv, bytes: 0 }, { ...csv, file: undefined },
    { ...csv, name: '../march.csv' }, { id: 'file' }, null])
    assert.match(sendProblem('Import this', [bad]), /Remove them/);
  assert.match(sendProblem('Import this', [csv, csv, csv, csv]), /at most 3/);
  assert.equal(attachmentNote([{ id: 'ID1', name: 'march.csv', size: 177, lines: 4 }]), '[Attached file: march.csv — attachment_id ID1, 177 bytes, 4 lines]');
});

test('a picked Excel statement can be sent as bytes; other spreadsheet formats and unreadable picks cannot', () => {
  const file = { text: async () => '', arrayBuffer: async () => new ArrayBuffer(8) };
  const book = { id: 'b', name: 'March statement.XLSX', bytes: 9000, file };
  assert.equal(sendableAttachment(book), true);
  assert.equal(sendProblem('Import this', [book, { id: 'a', name: 'march.csv', bytes: 100, file }]), null);
  assert.equal(isWorkbook(book.name), true); assert.equal(isWorkbook('march.csv'), false);
  for (const bad of [{ ...book, name: 'march.xls' }, { ...book, name: 'march.xlsm' }, { ...book, name: 'march.ods' }, { ...book, bytes: 524289 },
    { ...book, file: { text: async () => '' } }, { ...book, name: 'a/march.xlsx' }])
    assert.match(sendProblem('Import this', [bad]), /Only CSV or Excel \(\.xlsx\) files up to 512 KB/);
});
