import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { AgentSurface } from '../.test-build/AgentSurface.js';
import { compositionSchema } from '../.test-build/agent-chat.js';
import { AccountsTable, ActivityTable, CloseReviewView, JournalTable, ReconciliationView, ReportView, StatementsTable } from '../.test-build/LedgerViews.js';
import { parseCloseReview, parseFinancialReport, parseReconciliation, parseStatements } from '../.test-build/bookkeeping-client.js';

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

const BOOK = '7ca83106-36ad-444e-bf05-75e8bba58852', CASH = '0b1f1c1e-5a53-4f0c-9d63-0a4f8f3a2b11', RENT = '1b1f1c1e-5a53-4f0c-9d63-0a4f8f3a2b12';

test('ledger view definitions hold only exact book and account references, never rows or balances', () => {
  const stack = { id: 'ledger', type: 'Maipa.Stack', children: [
    { id: 'chart', type: 'Maipa.Accounts', props: { book_id: BOOK } },
    { id: 'entries', type: 'Maipa.Journal', props: { book_id: BOOK, show: 'pending' } },
    { id: 'checking', type: 'Maipa.AccountActivity', props: { book_id: BOOK, account_id: CASH } }] };
  assert.deepEqual(compositionSchema(stack), stack);
  for (const [type, props] of [
    ['Maipa.Accounts', undefined], ['Maipa.Accounts', {}], ['Maipa.Accounts', { book_id: 'books' }], ['Maipa.Accounts', { book_id: BOOK.toUpperCase() }],
    ['Maipa.Accounts', { book_id: BOOK, rows: [{ code: '1000', balance: '999999' }] }],
    ['Maipa.Journal', { book_id: BOOK }], ['Maipa.Journal', { book_id: BOOK, show: 'deleted' }], ['Maipa.Journal', { book_id: BOOK, show: 'all', entries: [] }],
    ['Maipa.AccountActivity', { book_id: BOOK }], ['Maipa.AccountActivity', { book_id: BOOK, account_id: 'checking' }],
    ['Maipa.AccountActivity', { book_id: BOOK, account_id: CASH, balance: '1.00' }],
  ]) assert.throws(() => compositionSchema({ id: 'bad', type, ...(props ? { props } : {}) }), undefined, `${type} ${JSON.stringify(props)}`);
  assert.throws(() => compositionSchema({ id: 'twice', type: 'Maipa.Stack', children: [stack.children[0], { ...stack.children[0], id: 'again' }] }));
  const html = renderToStaticMarkup(React.createElement(AgentSurface, { userId: '1', ui: { composition: { title: 'Ledger', schema: compositionSchema(stack) } } }));
  for (const label of ['Chart of accounts', 'Journal entries', 'Account activity']) assert.match(html, new RegExp(`aria-label="${label}"[^>]*aria-busy="true"`));
  assert.doesNotMatch(html, /999999/);
});

test('ledger views show exactly the server figures: balances, entry lines by account name, and running balances', () => {
  const accounts = [{ id: CASH, code: '1000', name: 'Checking', category: 'asset', cash: true }, { id: RENT, code: '6300', name: 'Rent <b>expense</b>', category: 'expense' }];
  const trial = [{ ...accounts[0], draft_debit: '0.0000', draft_credit: '25.0000', posted_debit: '1250.5000', posted_credit: '900.0000', balance_debit: '350.5000', balance_credit: '0.0000' },
    { ...accounts[1], draft_debit: '25.0000', draft_credit: '0.0000', posted_debit: '900.0000', posted_credit: '0.0000', balance_debit: '900.0000', balance_credit: '0.0000' }];
  const entry = (sequence, state, date, description, amount) => ({ id: `${sequence}b1f1c1e-5a53-4f0c-9d63-0a4f8f3a2b99`, book_id: BOOK, sequence, operation_id: CASH, payload_hash: 'a'.repeat(64),
    state, date, description, source: { kind: 'manual', reference: 'r' + sequence }, evidence: ['e'], debit: amount, credit: amount,
    lines: [{ account_id: RENT, debit: amount, credit: '0.0000' }, { account_id: CASH, debit: '0.0000', credit: amount }] });
  const snapshot = { actor_id: '1', book: { id: BOOK, entity_id: CASH, currency: 'USD', period_start: '2026-01-01', period_end: '2026-12-31', closed_through: null, revision: 4,
    accounts, journals: [entry(1, 'posted', '2026-03-03', 'March rent', '900.0000'), entry(3, 'draft', '2026-03-20', 'Bank fee', '25.0000')], has_more_journals: true },
    report: { book_id: BOOK, currency: 'USD', revision: 4, accounts: trial, posted_debit: '2150.5000', posted_credit: '900.0000', trial_debit: '1250.5000', trial_credit: '0.0000', pending_count: 1 } };
  const chart = renderToStaticMarkup(React.createElement(AccountsTable, { snapshot }));
  assert.match(chart, /data-account-count="2"/); assert.match(chart, /data-account-code="1000"/);
  assert.match(chart, /1000 · Checking/); assert.match(chart, /asset · bank or cash/); assert.equal(chart.split('bank or cash').length, 2); assert.match(chart, /350\.50/); assert.match(chart, /Rent &lt;b&gt;expense&lt;\/b&gt;/); assert.doesNotMatch(chart, /<b>expense/);
  assert.match(chart, /Debit balances 1,250\.50 · Credit balances 0\.00 · USD/);
  const all = renderToStaticMarkup(React.createElement(JournalTable, { snapshot, show: 'all' }));
  assert.match(all, /data-journal-count="2"/); assert.match(all, /March rent/); assert.match(all, /Posted/); assert.match(all, /Pending review/);
  assert.match(all, /6300 · Rent/); assert.match(all, /900\.00/); assert.match(all, /Showing the most recent entries only\./);
  const posted = renderToStaticMarkup(React.createElement(JournalTable, { snapshot, show: 'posted' }));
  assert.match(posted, /data-journal-count="1"/); assert.doesNotMatch(posted, /Bank fee/);
  const pending = renderToStaticMarkup(React.createElement(JournalTable, { snapshot, show: 'pending' }));
  assert.match(pending, /data-journal-count="1"/); assert.match(pending, /Bank fee/); assert.doesNotMatch(pending, /March rent/);
  const row = (sequence, date, description, debit, credit) => ({ journal_id: `${sequence}b1f1c1e-5a53-4f0c-9d63-0a4f8f3a2b99`, sequence, date, description, source: { kind: 'manual', reference: 'r' }, debit, credit });
  const activity = { book_id: BOOK, currency: 'USD', revision: 4, account: accounts[0], normal_side: 'debit',
    posted: [{ ...row(2, '2026-03-01', 'Client payment', '1250.5000', '0.0000'), balance: '1250.5000' }, { ...row(1, '2026-03-03', 'March rent', '0.0000', '900.0000'), balance: '350.5000' }],
    pending: [row(3, '2026-03-20', 'Bank fee', '0.0000', '25.0000')], posted_debit: '1250.5000', posted_credit: '900.0000', balance: '350.5000' };
  const shown = renderToStaticMarkup(React.createElement(ActivityTable, { activity }));
  assert.match(shown, /data-account-code="1000"/); assert.match(shown, /data-activity-balance="350\.5000"/); assert.match(shown, /data-activity-rows="2"/);
  assert.match(shown, /1000 · Checking/); assert.match(shown, /1,250\.50/); assert.match(shown, /Balance 350\.50 USD/);
  assert.match(shown, /1 entry is pending review and not in this balance\./);
  const overdrawn = renderToStaticMarkup(React.createElement(ActivityTable, { activity: { ...activity, posted: [], pending: [], balance: '-12.5000' } }));
  assert.match(overdrawn, /Balance −12\.50 USD/); assert.match(overdrawn, /No posted entries for this account yet\./);
});

const STATEMENT = '2b1f1c1e-5a53-4f0c-9d63-0a4f8f3a2b13';
const listed = { book_id: BOOK, revision: 9, statements: [
  { id: STATEMENT, filename: 'march <b>.csv', account_id: CASH, account: '1000 Checking', opening_balance: '1000.0000', closing_balance: '1340.5000',
    first_date: '2026-03-02', last_date: '2026-03-31', row_count: 3, can_reconcile: true, reconciliation: { recorded_at: '2026-11-19T10:00:00+00:00', as_of: '2026-03-31', unchanged: false } },
  { id: RENT, filename: 'april.csv', account_id: CASH, account: '1000 Checking', opening_balance: null, closing_balance: null,
    first_date: '2026-04-01', last_date: '2026-04-30', row_count: 12, can_reconcile: false, reconciliation: null },
  { id: CASH, filename: 'loose.csv', account_id: null, account: null, opening_balance: null, closing_balance: null,
    first_date: '2026-05-01', last_date: '2026-05-01', row_count: 1, can_reconcile: false, reconciliation: null }] };
const report = { book_id: BOOK, currency: 'USD', revision: 9,
  statement: { id: STATEMENT, filename: 'march.csv', account: { id: CASH, code: '1000', name: 'Checking' }, opening_balance: '1000.0000', closing_balance: '1340.5000', first_date: '2026-03-02', as_of: '2026-03-31' },
  book_balance: '1150.5000', unrecorded_lines: [{ row_id: RENT, date: '2026-03-31', description: 'Card fee', amount: '-10.0000', status: 'pending' }], unrecorded_total: '-10.0000',
  outstanding_entries: [{ journal_id: CASH, sequence: 7, date: '2026-03-30', description: 'Cheque 1042', amount: '-200.0000' }], outstanding_total: '-200.0000',
  adjusted_books: '1140.5000', adjusted_bank: '1140.5000', difference: '0.0000', reconciled: true, recorded: null };

test('statement and reconciliation definitions hold only exact references, never figures', () => {
  const stack = { id: 'bank', type: 'Maipa.Stack', children: [
    { id: 'statements', type: 'Maipa.Statements', props: { book_id: BOOK } },
    { id: 'reconcile', type: 'Maipa.Reconciliation', props: { book_id: BOOK, import_id: STATEMENT } }] };
  assert.deepEqual(compositionSchema(stack), stack);
  for (const [type, props] of [
    ['Maipa.Statements', undefined], ['Maipa.Statements', { book_id: 'books' }], ['Maipa.Statements', { book_id: BOOK, statements: [] }],
    ['Maipa.Reconciliation', { book_id: BOOK }], ['Maipa.Reconciliation', { book_id: BOOK, import_id: 'march' }],
    ['Maipa.Reconciliation', { book_id: BOOK, import_id: STATEMENT, difference: '0.00' }], ['Maipa.Reconciliation', { book_id: BOOK, import_id: STATEMENT, reconciled: true }],
  ]) assert.throws(() => compositionSchema({ id: 'bad', type, ...(props ? { props } : {}) }), undefined, `${type} ${JSON.stringify(props)}`);
  const html = renderToStaticMarkup(React.createElement(AgentSurface, { userId: '1', ui: { composition: { title: 'Bank', schema: compositionSchema(stack) } } }));
  for (const label of ['Bank statements', 'Bank reconciliation']) assert.match(html, new RegExp(`aria-label="${label}"[^>]*aria-busy="true"`));
});

test('statement lists and reconciliation reports are accepted only when exact and internally consistent', () => {
  assert.deepEqual(parseStatements(structuredClone(listed), BOOK), listed);
  assert.deepEqual(parseReconciliation(structuredClone(report), BOOK, STATEMENT), report);
  const first = listed.statements[0];
  for (const change of [{ can_reconcile: false }, { closing_balance: null }, { account: null }, { row_count: 0 }, { reconciliation: { recorded_at: 'x', as_of: '2026-03-31' } }, { balance: '1' }])
    assert.throws(() => parseStatements({ ...listed, statements: [{ ...first, ...change }] }, BOOK), undefined, JSON.stringify(change));
  assert.throws(() => parseStatements({ ...listed, book_id: STATEMENT }, BOOK));
  assert.throws(() => parseStatements({ ...listed, statements: [{ ...listed.statements[1], reconciliation: first.reconciliation }] }, BOOK));
  for (const change of [{ difference: '5.0000' }, { reconciled: false }, { adjusted_books: '1.0000' }, { unrecorded_total: '0.0000' }, { outstanding_total: '0.0000' },
    { book_id: STATEMENT }, { recorded: { as_of: '2026-03-31' } }, { adjustment: '1' }])
    assert.throws(() => parseReconciliation({ ...report, ...change }, BOOK, STATEMENT), undefined, JSON.stringify(change));
  assert.throws(() => parseReconciliation(report, BOOK, RENT));
  assert.throws(() => parseReconciliation({ ...report, unrecorded_lines: [{ ...report.unrecorded_lines[0], status: 'ignored' }] }, BOOK, STATEMENT));
  const off = { ...report, adjusted_bank: '1189.5000', difference: '-49.0000', reconciled: false, outstanding_entries: [{ ...report.outstanding_entries[0], amount: '-151.0000' }], outstanding_total: '-151.0000' };
  assert.equal(parseReconciliation(off, BOOK, STATEMENT).difference, '-49.0000');
});

test('the statement list says what each statement needs and the reconciliation shows exact figures and the difference', () => {
  const table = renderToStaticMarkup(React.createElement(StatementsTable, { list: listed }));
  assert.match(table, /data-statement-count="3"/); assert.match(table, /march &lt;b&gt;\.csv/); assert.doesNotMatch(table, /<b>\.csv/);
  assert.match(table, /1,000\.00/); assert.match(table, /1,340\.50/);
  assert.match(table, /Recorded as of 2026-03-31 · figures have changed since/);
  assert.match(table, /Needs its opening and closing balance/); assert.match(table, /Needs its bank account/);
  assert.match(table, /data-can-reconcile="true"/); assert.equal(table.split('data-can-reconcile="false"').length, 3);
  const agreed = renderToStaticMarkup(React.createElement(ReconciliationView, { report }));
  assert.match(agreed, /data-reconciled="true"/); assert.match(agreed, /data-difference="0\.0000"/);
  assert.match(agreed, /march\.csv · 1000 · Checking · as of 2026-03-31/);
  assert.match(agreed, /Bank closing balance/); assert.match(agreed, /1,340\.50/); assert.match(agreed, /Books balance/); assert.match(agreed, /1,150\.50/);
  assert.match(agreed, /Card fee/); assert.match(agreed, /−10\.00/); assert.match(agreed, /Cheque 1042/); assert.match(agreed, /−200\.00/);
  assert.match(agreed, /The statement agrees with your books\./); assert.match(agreed, /Not yet recorded as reviewed\./);
  const off = renderToStaticMarkup(React.createElement(ReconciliationView, { report: { ...report, adjusted_bank: '1189.5000', difference: '-49.0000', reconciled: false,
    outstanding_entries: [{ ...report.outstanding_entries[0], amount: '-151.0000' }], outstanding_total: '-151.0000',
    recorded: { recorded_at: '2026-11-19T10:00:00+00:00', as_of: '2026-03-31', unchanged: false } } }));
  assert.match(off, /data-reconciled="false"/); assert.match(off, /data-difference="-49\.0000"/);
  assert.match(off, /Unexplained difference of −49\.00 USD: the books are lower than the bank\./);
  assert.match(off, /Recorded as reviewed on 2026-11-19 as of 2026-03-31, but the figures have changed since\./);
  assert.doesNotMatch(off, /agrees with your books/);
});

const row = (id, code, name, category, amount) => ({ id, code, name, category, amount });
const statements = { book_id: BOOK, currency: 'USD', revision: 7, from: '2026-01-01', to: '2026-03-31', pending_count: 2,
  cash_flow: { accounts: [{ id: CASH, code: '1000', name: 'Checking', category: 'asset', cash: true, opening: '1000.0000', closing: '1350.5000' }],
    opening_cash: '1000.0000', closing_cash: '1350.5000', net_change: '350.5000', received: '1250.5000', paid: '-900.0000',
    movements: [{ id: STATEMENT, code: '4000', name: 'Sales <i>income</i>', category: 'income', cash: false, amount: '1250.5000' }, { id: RENT, code: '6300', name: 'Rent', category: 'expense', cash: false, amount: '-900.0000' }],
    bridge: { net_income: '350.5000', not_in_cash: '0.0000', other_accounts: '0.0000', net_change: '350.5000' } },
  income_statement: { income: [row(STATEMENT, '4000', 'Sales <i>income</i>', 'income', '1250.5000')], expenses: [row(RENT, '6300', 'Rent', 'expense', '900.0000')],
    total_income: '1250.5000', total_expenses: '900.0000', net_income: '350.5000' },
  balance_sheet: { as_of: '2026-03-31', assets: [{ ...row(CASH, '1000', 'Checking', 'asset', '1350.5000'), cash: true }], liabilities: [], equity: [row(BOOK, '3000', 'Owner equity', 'equity', '1000.0000')],
    total_assets: '1350.5000', total_liabilities: '0.0000', total_equity: '1000.0000', earnings_to_date: '350.5000', total_liabilities_and_equity: '1350.5000' } };

test('a report definition holds only its book and a real date range, never figures', () => {
  const good = { book_id: BOOK, from: '2026-01-01', to: '2026-03-31' };
  const stack = { id: 'results', type: 'Maipa.Stack', children: [{ id: 'quarter', type: 'Maipa.Report', props: good }] };
  assert.deepEqual(compositionSchema(stack), stack);
  assert.deepEqual(compositionSchema({ id: 'day', type: 'Maipa.Report', props: { ...good, from: '2024-02-29', to: '2024-02-29' } }).props.from, '2024-02-29');
  for (const props of [undefined, {}, { book_id: BOOK }, { ...good, book_id: 'books' }, { ...good, from: '2026-04-01' }, { ...good, to: '2026-02-30' }, { ...good, from: '2026-1-1' },
    { ...good, from: 20260101 }, { ...good, to: '2026-03-31T00:00:00' }, { ...good, net_income: '350.50' }, { ...good, income_statement: {} }, { ...good, compare_from: '2025-01-01', compare_to: '2025-03-31' }])
    assert.throws(() => compositionSchema({ id: 'bad', type: 'Maipa.Report', ...(props ? { props } : {}) }), undefined, JSON.stringify(props));
  assert.throws(() => compositionSchema({ id: 'twice', type: 'Maipa.Stack', children: [{ id: 'a', type: 'Maipa.Report', props: good }, { id: 'b', type: 'Maipa.Report', props: good }] }));
  const html = renderToStaticMarkup(React.createElement(AgentSurface, { userId: '1', ui: { composition: { title: 'Results', schema: compositionSchema(stack) } } }));
  assert.match(html, /aria-label="Financial statements"[^>]*aria-busy="true"/); assert.doesNotMatch(html, /350/);
});

test('the report view shows the income statement and balance sheet with exact totals from the books', () => {
  const html = renderToStaticMarkup(React.createElement(ReportView, { report: statements }));
  assert.match(html, /data-report-from="2026-01-01"/); assert.match(html, /data-report-to="2026-03-31"/);
  assert.match(html, /data-net-income="350\.5000"/); assert.match(html, /data-total-assets="1350\.5000"/); assert.match(html, /data-book-revision="7"/);
  assert.match(html, /Income statement · 2026-01-01 to 2026-03-31 · USD/); assert.match(html, /Balance sheet · as of 2026-03-31 · USD/);
  assert.match(html, /4000 · Sales &lt;i&gt;income&lt;\/i&gt;/); assert.doesNotMatch(html, /<i>income/);
  for (const text of ['Total income', 'Total expenses', 'Net income', 'Total assets', 'Total liabilities', 'Earnings to date', 'Total equity', 'Total liabilities and equity',
    '1,250.50', '900.00', '350.50', '1,350.50', '1,000.00', '1000 · Checking', '3000 · Owner equity', '6300 · Rent'])
    assert.equal(html.includes(text), true, text);
  assert.match(html, /2 pending entries are not included\./);
  const loss = structuredClone(statements);
  Object.assign(loss.income_statement, { income: [], total_income: '0.0000', net_income: '-900.0000' }); loss.pending_count = 0;
  Object.assign(loss.balance_sheet, { assets: [{ ...loss.balance_sheet.assets[0], amount: '100.0000' }], total_assets: '100.0000', earnings_to_date: '-900.0000', total_liabilities_and_equity: '100.0000' });
  const lost = renderToStaticMarkup(React.createElement(ReportView, { report: loss }));
  assert.match(lost, /data-net-income="-900\.0000"/); assert.match(lost, /Net loss/); assert.match(lost, /−900\.00/);
  assert.match(lost, /No income in this period\./); assert.doesNotMatch(lost, /pending/);
});

test('the cash flow is shown only when it reconciles, and an unmarked book says why there is none', () => {
  assert.deepEqual(parseFinancialReport(structuredClone(statements), BOOK, '2026-01-01', '2026-03-31'), statements);
  const html = renderToStaticMarkup(React.createElement(ReportView, { report: statements }));
  assert.match(html, /Cash flow · 2026-01-01 to 2026-03-31 · USD/); assert.match(html, /data-cash-flow="shown"/);
  assert.match(html, /data-net-cash="350\.5000"/); assert.match(html, /data-closing-cash="1350\.5000"/);
  for (const text of ['Cash at the start', 'Cash received', 'Cash paid', 'Change in cash', 'Cash at the end', '1000 · Checking at the end', '−900.00', '1,350.50'])
    assert.equal(html.includes(text), true, text);
  assert.match(html, /data-cash-bridge="350\.5000"/); assert.match(html, /Why cash changed differently from profit/);
  for (const text of ['Income and expenses not yet received or paid', 'Cash moved by other accounts']) assert.equal(html.includes(text), true, text);
  assert.equal(html.split('data-cash-movement=').length, 3); assert.equal(html.split('data-cash-account=').length, 2);
  assert.equal(html.split('data-account-code=').length, 5);   // The two statements keep their four account rows.
  const flow = statements.cash_flow;
  for (const change of [{ net_change: '350.0000' }, { closing_cash: '1350.0000' }, { opening_cash: '999.0000' }, { received: '1250.0000' }, { paid: '-899.0000' },
    { movements: [flow.movements[0]] }, { movements: [...flow.movements, flow.movements[0]] }, { movements: [{ ...flow.movements[0], cash: true }, flow.movements[1]] },
    { movements: [{ ...flow.movements[0], amount: '0.0000' }, flow.movements[1]] }, { accounts: [] }, { accounts: [{ ...flow.accounts[0], cash: false }] },
    { accounts: [{ ...flow.accounts[0], closing: '1.0000' }] }, { accounts: [{ ...flow.accounts[0], balance: '1' }] }, { note: 'x' },
    { bridge: { ...flow.bridge, net_income: '350.0000', not_in_cash: '0.5000' } }, { bridge: { ...flow.bridge, other_accounts: '1.0000' } },
    { bridge: { ...flow.bridge, net_change: '0.0000', not_in_cash: '-350.5000' } }, { bridge: { ...flow.bridge, note: 'x' } }, { bridge: undefined }])
    assert.throws(() => parseFinancialReport({ ...structuredClone(statements), cash_flow: { ...flow, ...change } }, BOOK, '2026-01-01', '2026-03-31'), undefined, JSON.stringify(change));
  const { cash_flow: _, ...without } = statements;
  assert.throws(() => parseFinancialReport(without, BOOK, '2026-01-01', '2026-03-31'));
  const unmarked = { ...statements, cash_flow: { accounts: [], opening_cash: '0.0000', closing_cash: '0.0000', net_change: '0.0000', received: '0.0000', paid: '0.0000', movements: [],
    bridge: { net_income: '350.5000', not_in_cash: '-350.5000', other_accounts: '0.0000', net_change: '0.0000' } } };
  const none = renderToStaticMarkup(React.createElement(ReportView, { report: parseFinancialReport(structuredClone(unmarked), BOOK, '2026-01-01', '2026-03-31') }));
  assert.match(none, /data-cash-flow="unmarked"/); assert.match(none, /No account is marked as a bank account or cash on hand yet/); assert.doesNotMatch(none, /Cash at the end/); assert.doesNotMatch(none, /data-cash-bridge/);
});

const closeReview = { book_id: BOOK, currency: 'USD', revision: 64, through: '2026-06-30', period_start: '2026-01-01', period_end: '2026-06-30', closed_through: '2026-02-28',
  calendar: [['01', '2026-01-31', 'closed'], ['02', '2026-02-28', 'closed'], ['03', '2026-03-31', 'open'], ['04', '2026-04-30', 'open'], ['05', '2026-05-31', 'open'], ['06', '2026-06-30', 'open']]
    .map(([m, ends, state]) => ({ month: `2026-${m}`, ends, state })),
  checks: [
    { key: 'drafts', ok: false, count: 1, items: [{ journal_id: RENT, sequence: 21, date: '2026-06-10', description: 'Office <b>supplies</b>', amount: '120.0000' }] },
    { key: 'statement_lines', ok: false, count: 2, items: [{ row_id: CASH, date: '2026-06-03', description: 'Card fee', amount: '-10.0000', status: 'pending' },
      { row_id: STATEMENT, date: '2026-06-05', description: 'Client payment', amount: '1250.5000', status: 'proposed' }] },
    { key: 'reconciliations', ok: false, count: 1, items: [{ import_id: STATEMENT, filename: 'june.csv', last_date: '2026-06-05', account: '1000 Checking', state: 'needs_balances' }] },
    { key: 'integrity', ok: true, count: 0, items: [] }],
  can_close: false, blocking: 'ledger_period_has_drafts', ready: false };

test('a close review definition holds only its book and a real date, never results', () => {
  const good = { book_id: BOOK, through: '2026-06-30' };
  const one = { id: 'june-close', type: 'Maipa.CloseReview', props: good };
  assert.deepEqual(compositionSchema(one), one);
  for (const props of [undefined, {}, { book_id: BOOK }, { ...good, book_id: 'books' }, { ...good, through: '2026-02-30' }, { ...good, through: '2026-6-30' },
    { ...good, through: 20260630 }, { ...good, ready: true }, { ...good, checks: [] }])
    assert.throws(() => compositionSchema({ id: 'bad', type: 'Maipa.CloseReview', ...(props ? { props } : {}) }), undefined, JSON.stringify(props));
  assert.throws(() => compositionSchema({ id: 'twice', type: 'Maipa.Stack', children: [{ id: 'a', type: 'Maipa.CloseReview', props: good }, { id: 'b', type: 'Maipa.CloseReview', props: good }] }));
  const html = renderToStaticMarkup(React.createElement(AgentSurface, { userId: '1', ui: { composition: { title: 'Closing June', schema: compositionSchema(one) } } }));
  assert.match(html, /aria-label="Close review"[^>]*aria-busy="true"/);
});

test('a close review is accepted only when it is consistent, and shows each check with its items', () => {
  assert.deepEqual(parseCloseReview(structuredClone(closeReview), BOOK, '2026-06-30'), closeReview);
  const html = renderToStaticMarkup(React.createElement(CloseReviewView, { review: closeReview }));
  assert.match(html, /data-close-through="2026-06-30"/); assert.match(html, /data-close-ready="false"/); assert.match(html, /data-close-can="false"/);
  assert.match(html, /Closing through 2026-06-30/); assert.match(html, /Closed through 2026-02-28/);
  assert.equal(html.split('data-close-month=').length, 7); assert.equal(html.split('data-month-state="closed"').length, 3);
  for (const text of ['Draft entries', 'Statement lines without an entry', 'Statements not reconciled', 'Entries pass the integrity check',
    'A draft entry is dated in this period, so the books cannot be closed until it is posted or discarded.', 'Card fee', '−10.00', 'Client payment', '1,250.50',
    'june.csv', 'Needs its opening and closing balance', '#21 Office &lt;b&gt;supplies&lt;/b&gt;', '120.00', 'Entry awaiting your review'])
    assert.equal(html.includes(text), true, text);
  assert.doesNotMatch(html, /<b>supplies/);
  assert.equal(html.split('data-check-ok="false"').length, 4); assert.equal(html.split('data-check-ok="true"').length, 2);
  // Integrity findings exactly as the server reports them, with and without an entry.
  const damaged = { ...closeReview, checks: [...closeReview.checks.slice(0, 3), { key: 'integrity', ok: false, count: 2,
    items: [{ code: 'journal_unbalanced', journal_id: RENT, sequence: 21 }, { code: 'event_history_incomplete', journal_id: null, sequence: null }] }] };
  const findings = renderToStaticMarkup(React.createElement(CloseReviewView, { review: parseCloseReview(structuredClone(damaged), BOOK, '2026-06-30') }));
  assert.match(findings, /2 findings: ask Maipa to explain them before closing\./); assert.equal(findings.split('data-check-ok="false"').length, 5);
  const clear = { ...closeReview, closed_through: null, checks: closeReview.checks.map(c => ({ ...c, ok: true, count: 0, items: [] })), can_close: true, blocking: null, ready: true,
    calendar: closeReview.calendar.map(m => ({ ...m, state: 'open' })) };
  const ready = renderToStaticMarkup(React.createElement(CloseReviewView, { review: parseCloseReview(structuredClone(clear), BOOK, '2026-06-30') }));
  assert.match(ready, /data-close-ready="true"/); assert.match(ready, /Nothing is outstanding\. Closing stays your decision\./); assert.doesNotMatch(ready, /Closed through/);
  const closed = { ...clear, can_close: false, blocking: 'ledger_period_already_closed', ready: false };
  assert.match(renderToStaticMarkup(React.createElement(CloseReviewView, { review: parseCloseReview(structuredClone(closed), BOOK, '2026-06-30') })), /already closed through this date or later/);
  for (const change of [{ ready: true }, { can_close: true }, { blocking: 'something_else' }, { through: '2026-05-31' }, { book_id: RENT },
    { checks: closeReview.checks.slice(1) }, { checks: [{ ...closeReview.checks[0], ok: true }, ...closeReview.checks.slice(1)] },
    { checks: [{ ...closeReview.checks[0], count: 2 }, ...closeReview.checks.slice(1)] }, { checks: [...closeReview.checks.slice(0, 3), { ...closeReview.checks[3], key: 'other' }] },
    { checks: [closeReview.checks[0], { ...closeReview.checks[1], items: [{ ...closeReview.checks[1].items[0], status: 'posted' }, closeReview.checks[1].items[1]] }, ...closeReview.checks.slice(2)] },
    { checks: [...closeReview.checks.slice(0, 2), { ...closeReview.checks[2], items: [{ ...closeReview.checks[2].items[0], state: 'fine' }] }, closeReview.checks[3]] },
    { calendar: [{ month: '2026-01', ends: '2026-01-31', state: 'paused' }] }, { note: 'x' },
    { checks: [...closeReview.checks.slice(0, 3), { key: 'integrity', ok: false, count: 1, items: [{ code: 'journal_unbalanced', journal_id: RENT }] }] },
    { checks: [...closeReview.checks.slice(0, 3), { key: 'integrity', ok: false, count: 1, items: [{ code: 'Bad Code', journal_id: null, sequence: null }] }] }])
    assert.throws(() => parseCloseReview({ ...structuredClone(closeReview), ...structuredClone(change) }, BOOK, '2026-06-30'), undefined, JSON.stringify(change));
});
