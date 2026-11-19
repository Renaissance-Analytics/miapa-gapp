"""Stated balances can be added to an earlier statement, and an agreeing reconciliation is recorded by approval."""
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.maipa.approvals import services as approvals
from backend.maipa.approvals.models import ActionRequest
from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.ledger import services as ledger
from backend.maipa.ledger.models import LedgerEvent, Reconciliation, SourceImport
from backend.maipa.workspace.models import Installation, Membership

PERIOD = {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'}
MARCH = 'date,description,amount\n2026-03-02,Client payment,1250.5\n2026-03-05,Synthetic rent,-900\n'


class ReconciliationRecordTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='record-owner')
        cls.other = get_user_model().objects.create_user(username='record-other')
        Membership.objects.create(user=cls.user, installation=install, role='preparer')
        Membership.objects.create(user=cls.other, installation=install, role='administrator')
        cls.entity = Entity.objects.create(engagement=Engagement.objects.create(
            owner=cls.user, installation=install, name='Synthetic records'), name='Synthetic record business')

    def setUp(self):
        self.book = ledger.create_book(self.user, self.entity.pk, PERIOD)
        self.accounts = {a['code']: a['id'] for a in self.book['accounts']}
        ledger.post_opening(self.user, self.book['id'], {'expected_revision': 0,
            'balances': [{'account_id': self.accounts['1000'], 'debit': '1000', 'credit': '0'}], 'control': {'debit': '1000', 'credit': '0'}})
        self.statement = ledger.import_statement(self.user, self.book['id'], {'expected_revision': self.revision(), 'filename': 'march.csv',
                                                                             'content': MARCH, 'account_id': self.accounts['1000']})
        self.rows = {row['description']: row for row in ledger.source_rows(self.user, self.book['id'])['rows']}

    def revision(self):
        return ledger.read_book(self.user, self.book['id'])['revision']

    def entry(self, row, lines):
        made = ledger.propose_journal(self.user, self.book['id'], {
            'operation_id': str(uuid.uuid4()), 'expected_revision': self.revision(), 'date': row['date'], 'description': row['description'],
            'source': {'kind': 'csv', 'reference': row['id']}, 'evidence': [row['id']],
            'lines': [{'account_id': self.accounts[code], 'debit': debit, 'credit': credit} for code, debit, credit in lines]})
        ledger.confirm_own_journal(self.user, self.book['id'], made['id'],
            {'expected_revision': self.revision(), 'payload_hash': made['payload_hash'], 'confirm': True})
        return made

    def post_both(self):
        self.entry(self.rows['Client payment'], [('1000', '1250.5', '0'), ('4000', '0', '1250.5')])
        return self.entry(self.rows['Synthetic rent'], [('6200', '900', '0'), ('1000', '0', '900')])

    def ask(self, operation, user=None, **changes):
        payload = {'book_id': self.book['id'], 'import_id': self.statement['id']} | changes
        return approvals.request_action(user or self.user, {'operation': operation, 'summary': 'Synthetic request', 'payload': payload})

    def approve(self, item):
        return approvals.decide(self.user, item['id'], {'decision': 'approve', 'payload_hash': item['payload_hash']})

    def balances(self, **changes):
        return self.ask('statement.balances', **({'opening_balance': '1000', 'closing_balance': '1350.5'} | changes))

    def listed(self):
        return next(item for item in ledger.statements(self.user, self.book['id'])['statements'] if item['id'] == self.statement['id'])

    def test_approved_balances_are_added_to_a_statement_whose_rows_add_up_between_them(self):
        item = self.balances()
        self.assertEqual(item['detail'], {'books': 'Synthetic record business', 'currency': 'USD', 'statement': 'march.csv', 'account': '1000 Checking',
            'row_count': 2, 'net': '350.5000', 'opening_balance': '1000.0000', 'closing_balance': '1350.5000', 'current': True})
        self.assertEqual((self.listed()['can_reconcile'], self.listed()['closing_balance']), (False, None))
        before = self.revision()
        done = self.approve(item)
        self.assertEqual((done['state'], done['result']['opening_balance'], done['result']['closing_balance']), ('approved', '1000.0000', '1350.5000'))
        self.assertEqual((self.listed()['can_reconcile'], self.listed()['closing_balance']), (True, '1350.5000'))
        event = LedgerEvent.objects.get(book_id=self.book['id'], revision=before + 1)
        self.assertEqual((event.action, str(event.target)), ('statement.balances', self.statement['id']))
        self.assertEqual(self.approve(item)['result'], done['result'])
        self.assertEqual(self.revision(), before + 1)

    def test_balances_that_do_not_add_up_are_malformed_or_would_replace_stated_ones_are_refused(self):
        for changes, code in (({'closing_balance': '1350'}, 'import_balance_mismatch'), ({'opening_balance': '999'}, 'import_balance_mismatch'),
                              ({'opening_balance': 'a thousand'}, 'invalid_ledger_request'), ({'closing_balance': '1350.50001'}, 'invalid_ledger_request'),
                              ({'import_id': str(uuid.uuid4())}, 'import_not_found')):
            with self.assertRaises(approvals.ApprovalError, msg=changes) as refused:
                self.balances(**changes)
            self.assertEqual(refused.exception.code, code, changes)
        with self.assertRaises(approvals.ApprovalError):
            self.ask('statement.balances', self.other, opening_balance='1000', closing_balance='1350.5')
        with self.assertRaises(approvals.ApprovalError):
            self.balances(note='x')
        self.assertEqual(ActionRequest.objects.count(), 0)
        first, second = self.balances(), self.balances()
        self.approve(first)
        stale = self.approve(second)
        self.assertEqual((stale['state'], stale['error']), ('failed', 'import_balances_already_set'))
        with self.assertRaises(approvals.ApprovalError) as refused:
            self.balances()
        self.assertEqual(refused.exception.code, 'import_balances_already_set')
        self.assertEqual((SourceImport.objects.get().opening_units, SourceImport.objects.get().closing_units), (10000000, 13505000))

    def test_an_agreeing_reconciliation_is_recorded_once_with_its_exact_figures(self):
        self.approve(self.balances())
        self.post_both()
        item = self.ask('reconciliation.record')
        self.assertEqual(item['detail'], {'books': 'Synthetic record business', 'currency': 'USD', 'statement': 'march.csv', 'account': '1000 Checking',
            'as_of': '2026-03-05', 'closing_balance': '1350.5000', 'book_balance': '1350.5000', 'unrecorded_total': '0.0000', 'outstanding_total': '0.0000',
            'difference': '0.0000', 'reconciled': True})
        self.assertEqual(Reconciliation.objects.count(), 0)
        before = self.revision()
        done = self.approve(item)
        self.assertEqual((done['state'], done['result']['as_of'], done['result']['closing_balance'], done['result']['book_balance']),
                         ('approved', '2026-03-05', '1350.5000', '1350.5000'))
        saved = Reconciliation.objects.get()
        self.assertEqual((str(saved.statement_id), saved.closing_units, saved.book_units, saved.unrecorded_units, saved.outstanding_units, saved.as_of.isoformat()),
                         (self.statement['id'], 13505000, 13505000, 0, 0, '2026-03-05'))
        event = LedgerEvent.objects.get(book_id=self.book['id'], revision=before + 1)
        self.assertEqual((event.action, str(event.target)), ('reconciliation.record', self.statement['id']))
        self.assertEqual(self.listed()['reconciliation'], {'recorded_at': saved.created_at.isoformat(), 'as_of': '2026-03-05', 'unchanged': True})
        self.assertEqual(ledger.reconciliation(self.user, self.book['id'], {'import_id': self.statement['id']})['recorded'], self.listed()['reconciliation'])
        self.assertEqual(self.approve(item)['result'], done['result'])
        with self.assertRaises(approvals.ApprovalError) as refused:
            self.ask('reconciliation.record')
        self.assertEqual(refused.exception.code, 'reconciliation_already_recorded')
        self.assertEqual(Reconciliation.objects.count(), 1)

    def test_a_reconciliation_with_a_difference_or_without_balances_cannot_be_recorded(self):
        with self.assertRaises(approvals.ApprovalError) as refused:
            self.ask('reconciliation.record')
        self.assertEqual(refused.exception.code, 'reconciliation_balances_required')
        self.approve(self.balances(opening_balance='900', closing_balance='1250.5'))      # The bank says 100.00 less than the books opened with.
        self.post_both()
        with self.assertRaises(approvals.ApprovalError) as refused:
            self.ask('reconciliation.record')
        self.assertEqual(refused.exception.code, 'reconciliation_difference')
        for user, changes in ((self.other, {}), (self.user, {'import_id': str(uuid.uuid4())}), (self.user, {'force': True})):
            with self.assertRaises(approvals.ApprovalError):
                self.ask('reconciliation.record', user, **changes)
        self.assertEqual((Reconciliation.objects.count(), self.listed()['reconciliation']), (0, None))

    def test_a_recorded_reconciliation_reports_when_later_changes_alter_its_figures(self):
        self.approve(self.balances())
        rent = self.post_both()
        first, second = self.ask('reconciliation.record'), self.ask('reconciliation.record')
        self.approve(first)
        stale = self.approve(second)
        self.assertEqual((stale['state'], stale['error']), ('failed', 'reconciliation_already_recorded'))
        self.assertEqual(self.listed()['reconciliation']['unchanged'], True)
        ledger.reverse_journal(self.user, self.book['id'], rent['id'], {'expected_revision': self.revision(), 'date': '2026-03-05'})
        self.assertEqual(self.listed()['reconciliation']['unchanged'], False)
        report = ledger.reconciliation(self.user, self.book['id'], {'import_id': self.statement['id']})
        self.assertEqual((report['recorded']['unchanged'], report['reconciled'], [line['description'] for line in report['unrecorded_lines']]),
                         (False, True, ['Synthetic rent']))
