"""Bank reconciliation: the statement's closing balance against the books, with every item that explains the difference."""
import json
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.ledger import services as ledger
from backend.maipa.workspace.models import Installation, Membership

PERIOD = {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'}
MARCH = 'date,description,amount\n2026-03-02,Client payment,1250.5\n2026-03-05,Synthetic rent,-900\n2026-03-31,Card fee,-10\n'


class BankReconciliationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='reconcile-owner')
        cls.other = get_user_model().objects.create_user(username='reconcile-other')
        Membership.objects.create(user=cls.user, installation=install, role='preparer')
        Membership.objects.create(user=cls.other, installation=install, role='administrator')
        cls.entity = Entity.objects.create(engagement=Engagement.objects.create(
            owner=cls.user, installation=install, name='Synthetic reconciliations'), name='Synthetic reconciliation business')

    def setUp(self):
        self.book = ledger.create_book(self.user, self.entity.pk, PERIOD)
        self.accounts = {a['code']: a['id'] for a in self.book['accounts']}

    def revision(self):
        return ledger.read_book(self.user, self.book['id'])['revision']

    def opening(self, amount='1000'):
        ledger.post_opening(self.user, self.book['id'], {'expected_revision': self.revision(),
            'balances': [{'account_id': self.accounts['1000'], 'debit': amount, 'credit': '0'}], 'control': {'debit': amount, 'credit': '0'}})

    def statement(self, content=MARCH, name='march.csv', **extra):
        base = {'account_id': self.accounts['1000'], 'opening_balance': '1000', 'closing_balance': '1340.5'}
        return ledger.import_statement(self.user, self.book['id'], {'expected_revision': self.revision(), 'filename': name, 'content': content,
                                                                   **{k: v for k, v in (base | extra).items() if v is not None}})

    def row(self, description):
        return next(r for r in ledger.source_rows(self.user, self.book['id'])['rows'] if r['description'] == description)

    def entry(self, date, description, lines, row=None, post=True):
        reference = row['id'] if row else f'synthetic-{uuid.uuid4()}'
        made = ledger.propose_journal(self.user, self.book['id'], {
            'operation_id': str(uuid.uuid4()), 'expected_revision': self.revision(), 'date': date, 'description': description,
            'source': {'kind': 'csv' if row else 'manual', 'reference': reference}, 'evidence': [reference],
            'lines': [{'account_id': self.accounts[code], 'debit': debit, 'credit': credit} for code, debit, credit in lines]})
        if post:
            ledger.confirm_own_journal(self.user, self.book['id'], made['id'],
                {'expected_revision': self.revision(), 'payload_hash': made['payload_hash'], 'confirm': True})
        return made

    def books(self):
        """Opening 1,000.00; the payment and rent posted from the statement; the fee not yet; a 200.00 cheque the bank has not seen."""
        self.opening()
        saved = self.statement()
        self.entry('2026-03-02', 'Client payment', [('1000', '1250.5', '0'), ('4000', '0', '1250.5')], self.row('Client payment'))
        self.entry('2026-03-05', 'Synthetic rent', [('6200', '900', '0'), ('1000', '0', '900')], self.row('Synthetic rent'))
        cheque = self.entry('2026-03-30', 'Cheque 1042 to a supplier', [('6200', '200', '0'), ('1000', '0', '200')])
        return saved, cheque

    def report(self, saved, user=None):
        return ledger.reconciliation(user or self.user, self.book['id'], {'import_id': saved['id']})

    def test_statement_and_books_agree_once_unrecorded_lines_and_outstanding_entries_are_counted(self):
        saved, cheque = self.books()
        result = self.report(saved)
        self.assertEqual(result['statement'], {'id': saved['id'], 'filename': 'march.csv', 'account': {'id': self.accounts['1000'], 'code': '1000', 'name': 'Checking'},
            'opening_balance': '1000.0000', 'closing_balance': '1340.5000', 'first_date': '2026-03-02', 'as_of': '2026-03-31'})
        self.assertEqual((result['book_balance'], result['currency']), ('1150.5000', 'USD'))
        self.assertEqual(result['unrecorded_lines'], [{'row_id': self.row('Card fee')['id'], 'date': '2026-03-31', 'description': 'Card fee', 'amount': '-10.0000', 'status': 'pending'}])
        self.assertEqual(result['outstanding_entries'], [{'journal_id': cheque['id'], 'sequence': cheque['sequence'], 'date': '2026-03-30',
                                                          'description': 'Cheque 1042 to a supplier', 'amount': '-200.0000'}])
        self.assertEqual((result['unrecorded_total'], result['outstanding_total'], result['adjusted_books'], result['adjusted_bank'], result['difference'], result['reconciled']),
                         ('-10.0000', '-200.0000', '1140.5000', '1140.5000', '0.0000', True))
        self.assertEqual(self.report(saved), result)

    def test_a_draft_entry_or_one_dated_after_the_statement_leaves_its_line_unrecorded_as_of_that_date(self):
        saved, _ = self.books()
        fee = self.row('Card fee')
        draft = self.entry('2026-03-31', 'Card fee', [('6100', '10', '0'), ('1000', '0', '10')], fee, post=False)
        result = self.report(saved)
        self.assertEqual(([line['status'] for line in result['unrecorded_lines']], result['book_balance'], result['reconciled']), (['proposed'], '1150.5000', True))
        ledger.confirm_own_journal(self.user, self.book['id'], draft['id'], {'expected_revision': self.revision(), 'payload_hash': draft['payload_hash'], 'confirm': True})
        posted = self.report(saved)
        self.assertEqual((posted['unrecorded_lines'], posted['book_balance'], posted['adjusted_books'], posted['reconciled']), ([], '1140.5000', '1140.5000', True))

    def test_a_missing_opening_balance_or_a_wrong_entry_shows_as_an_unexplained_difference(self):
        saved = self.statement()
        self.entry('2026-03-02', 'Client payment', [('1000', '1250.5', '0'), ('4000', '0', '1250.5')], self.row('Client payment'))
        result = self.report(saved)
        self.assertEqual((result['book_balance'], result['adjusted_books'], result['adjusted_bank'], result['difference'], result['reconciled']),
                         ('1250.5000', '340.5000', '1340.5000', '-1000.0000', False))
        self.assertEqual(sorted(line['description'] for line in result['unrecorded_lines']), ['Card fee', 'Synthetic rent'])

    def test_a_reversed_pair_is_not_listed_and_a_duplicate_or_later_line_is_not_counted(self):
        saved, cheque = self.books()
        ledger.reverse_journal(self.user, self.book['id'], cheque['id'], {'expected_revision': self.revision(), 'date': '2026-03-31'})
        self.statement('date,description,amount\n2026-03-31,Card fee,-10\n2026-04-03,April deposit,75\n', 'april.csv',
                       opening_balance='1340.5', closing_balance='1405.5')
        repeat = next(r for r in ledger.source_rows(self.user, self.book['id'])['rows'] if r['import_filename'] == 'april.csv' and r['description'] == 'Card fee')
        ledger.mark_duplicate(self.user, self.book['id'], {'expected_revision': self.revision(), 'row_id': repeat['id'], 'of_row_id': self.row('Card fee')['id']})
        result = self.report(saved)
        self.assertEqual((result['outstanding_entries'], result['outstanding_total'], [line['description'] for line in result['unrecorded_lines']],
                          result['book_balance'], result['adjusted_books'], result['adjusted_bank'], result['reconciled']),
                         ([], '0.0000', ['Card fee'], '1350.5000', '1340.5000', '1340.5000', True))

    def test_statements_without_balances_or_a_bank_account_and_foreign_readers_are_refused(self):
        self.opening()
        bare = self.statement(opening_balance=None, closing_balance=None)
        loose = self.statement('date,description,amount\n2026-05-01,Loose line,5\n', 'loose.csv', account_id=None, opening_balance='0', closing_balance='5')
        for value, code in (({'import_id': bare['id']}, 'reconciliation_balances_required'), ({'import_id': loose['id']}, 'match_account_required'),
                            ({'import_id': str(uuid.uuid4())}, 'import_not_found'), ({'import_id': 'march'}, 'import_not_found'),
                            ({}, 'invalid_ledger_request'), ({'import_id': bare['id'], 'as_of': '2026-03-31'}, 'invalid_ledger_request')):
            with self.assertRaises(ledger.LedgerError, msg=value) as refused:
                ledger.reconciliation(self.user, self.book['id'], value)
            self.assertEqual(refused.exception.code, code, value)
        with self.assertRaises(ledger.LedgerError):
            self.report(bare, self.other)

    def test_statements_are_listed_with_what_each_needs_before_it_can_be_reconciled(self):
        self.opening()
        full = self.statement()
        bare = self.statement('date,description,amount\n2026-04-01,April line,5\n', 'april.csv', opening_balance=None, closing_balance=None)
        loose = self.statement('date,description,amount\n2026-05-01,Loose line,5\n', 'loose.csv', account_id=None, opening_balance=None, closing_balance=None)
        listed = {item['filename']: item for item in ledger.statements(self.user, self.book['id'])['statements']}
        self.assertEqual(listed['march.csv'], {'id': full['id'], 'filename': 'march.csv', 'account_id': self.accounts['1000'], 'account': '1000 Checking',
            'opening_balance': '1000.0000', 'closing_balance': '1340.5000', 'first_date': '2026-03-02', 'last_date': '2026-03-31', 'row_count': 3, 'can_reconcile': True, 'reconciliation': None})
        self.assertEqual((listed['april.csv']['can_reconcile'], listed['april.csv']['closing_balance'], listed['april.csv']['id']), (False, None, bare['id']))
        self.assertEqual((listed['loose.csv']['can_reconcile'], listed['loose.csv']['account'], listed['loose.csv']['id']), (False, None, loose['id']))

    def test_http_and_the_agent_tool_return_exactly_the_service_results(self):
        from tests.test_bookkeeping_posting import BookkeepingPostingTests
        saved, _ = self.books()
        self.client.force_login(self.user)
        actor = {'HTTP_X_MAIPA_AGENT_ACTOR': str(self.user.pk)}
        url = f"/api/books/{self.book['id']}/reconciliation/"
        self.assertEqual(self.client.get(url, secure=True, **actor).json(), ledger.statements(self.user, self.book['id']))
        answer = self.client.get(url + '?import=' + saved['id'], secure=True, **actor)
        self.assertEqual((answer.status_code, answer.json()), (200, self.report(saved)))
        self.assertEqual(self.client.get(url + '?import=march', secure=True, **actor).status_code, 400)
        self.assertEqual(self.client.get(url + '?import=' + saved['id'] + '&as_of=2026-03-31', secure=True, **actor).status_code, 400)
        self.assertEqual(self.client.get(url, secure=True).status_code, 409)
        self.assertEqual(self.client.post(url, data='{}', content_type='application/json', secure=True, **actor).status_code, 405)
        broker, _, _ = BookkeepingPostingTests.broker(self)
        tool = lambda arguments: broker.call('maipa_books_reconcile', {'book_id': self.book['id'], **arguments})
        self.assertEqual(json.loads(tool({})['content'][0]['text']), ledger.statements(self.user, self.book['id']))
        self.assertEqual(json.loads(tool({'import_id': saved['id']})['content'][0]['text']), self.report(saved))
        self.assertTrue(tool({'import_id': saved['id'], 'adjust': True})['isError'])
        self.assertTrue(tool({'import_id': str(uuid.uuid4())})['isError'])
