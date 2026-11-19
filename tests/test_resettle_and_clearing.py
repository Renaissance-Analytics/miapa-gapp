"""A statement line whose entry was reversed needs settling again; a processor payout is one entry net of fees."""
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.maipa.approvals import services as approvals
from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.ledger import services as ledger
from backend.maipa.ledger.models import Journal, SourceRowMatch
from backend.maipa.workspace.models import Installation, Membership

PERIOD = {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'}
CHECKING = 'date,description,amount\n2026-03-02,Transfer to savings,-500\n2026-03-05,Synthetic rent,-900\n2026-03-09,Processor payout,970\n'
SAVINGS = 'date,description,amount\n2026-03-03,Transfer from checking,500\n'


class ResettleAndClearingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='resettle-owner')
        Membership.objects.create(user=cls.user, installation=install, role='preparer')
        cls.entity = Entity.objects.create(engagement=Engagement.objects.create(
            owner=cls.user, installation=install, name='Synthetic resettle'), name='Synthetic resettle business')

    def setUp(self):
        self.book = ledger.create_book(self.user, self.entity.pk, PERIOD)
        self.accounts = {a['code']: a['id'] for a in self.book['accounts']}
        savings = ledger.create_account(self.user, self.book['id'], {'expected_revision': 0, 'code': '1010', 'name': 'Savings', 'category': 'asset'})
        ledger.set_account_cash(self.user, self.book['id'], savings['id'], {'expected_revision': self.revision(), 'cash': True})
        self.accounts['1010'] = savings['id']
        for name, content, code in (('checking.csv', CHECKING, '1000'), ('savings.csv', SAVINGS, '1010')):
            ledger.import_statement(self.user, self.book['id'], {'expected_revision': self.revision(), 'filename': name, 'content': content,
                                                                'account_id': self.accounts[code]})
        self.rows = {row['description']: row for row in ledger.source_rows(self.user, self.book['id'])['rows']}

    def revision(self):
        return ledger.read_book(self.user, self.book['id'])['revision']

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

    def reverse(self, made):
        return ledger.reverse_journal(self.user, self.book['id'], made['id'], {'expected_revision': self.revision(), 'date': '2026-03-31'})

    def match(self, row, made):
        item = approvals.request_action(self.user, {'operation': 'statement.match', 'summary': 'Match the line',
            'payload': {'book_id': self.book['id'], 'row_id': row['id'], 'journal_id': made['id']}})
        return approvals.decide(self.user, item['id'], {'decision': 'approve', 'payload_hash': item['payload_hash']})

    def state(self, description):
        row = next(r for r in ledger.source_rows(self.user, self.book['id'])['rows'] if r['description'] == description)
        return row['status'], row['via']

    def balance(self, code):
        report = ledger.journal_preview(self.user, self.book['id'])
        account = next(a for a in report['accounts'] if a['code'] == code)
        return account['balance_debit'], account['balance_credit']

    def test_a_line_whose_own_entry_was_reversed_is_pending_again_and_is_settled_by_matching_a_corrected_entry(self):
        rent = self.rows['Synthetic rent']
        wrong = self.entry(rent['date'], 'Rent booked to the wrong account', [('6100', '900', '0'), ('1000', '0', '900')], rent)
        self.assertEqual(self.state('Synthetic rent'), ('posted', 'entry'))
        self.reverse(wrong)
        self.assertEqual(self.state('Synthetic rent'), ('pending', None))
        with self.assertRaises(ledger.LedgerError) as refused:
            self.entry(rent['date'], 'Rent again', [('6200', '900', '0'), ('1000', '0', '900')], rent, post=False)
        self.assertEqual(refused.exception.code, 'ledger_source_exists')       # The line keeps one source entry; a correction is matched instead.
        with self.assertRaises(approvals.ApprovalError) as refused:
            self.match(rent, wrong)
        self.assertEqual(refused.exception.code, 'match_entry_reversed')
        right = self.entry(rent['date'], 'Synthetic rent', [('6200', '900', '0'), ('1000', '0', '900')])
        self.assertEqual(ledger.match_candidates(self.user, self.book['id'])['entries'], [{'row_id': rent['id'], 'journal_id': right['id'], 'days_apart': 0}])
        self.assertEqual(self.match(rent, right)['state'], 'approved')
        self.assertEqual(self.state('Synthetic rent'), ('posted', 'match'))
        self.assertEqual((self.balance('6100'), self.balance('6200'), self.balance('1000')),
                         (('0.0000', '0.0000'), ('900.0000', '0.0000'), ('0.0000', '900.0000')))
        self.assertEqual(ledger.verify_book(self.user, self.book['id'])['ok'], True)

    def test_a_reversed_transfer_frees_both_lines_and_one_new_entry_settles_both(self):
        out, into = self.rows['Transfer to savings'], self.rows['Transfer from checking']
        first = self.entry(out['date'], 'Transfer to savings', [('1010', '500', '0'), ('1000', '0', '500')], out)
        self.match(into, first)
        self.assertEqual((self.state('Transfer to savings'), self.state('Transfer from checking')), (('posted', 'entry'), ('posted', 'match')))
        self.reverse(first)
        self.assertEqual((self.state('Transfer to savings'), self.state('Transfer from checking')), (('pending', None), ('pending', None)))
        self.assertEqual(ledger.match_candidates(self.user, self.book['id'])['transfers'],
                         [{'out_row_id': out['id'], 'in_row_id': into['id'], 'amount': '500.0000', 'days_apart': 1}])
        again = self.entry(out['date'], 'Transfer to savings, corrected', [('1010', '500', '0'), ('1000', '0', '500')])
        self.assertEqual((self.match(out, again)['state'], self.match(into, again)['state']), ('approved', 'approved'))
        self.assertEqual((self.state('Transfer to savings'), self.state('Transfer from checking')), (('posted', 'match'), ('posted', 'match')))
        with self.assertRaises(approvals.ApprovalError) as refused:
            self.match(into, again)
        self.assertEqual(refused.exception.code, 'match_row_not_pending')
        self.assertEqual((self.balance('1010'), self.balance('1000'), SourceRowMatch.objects.count()), (('500.0000', '0.0000'), ('0.0000', '500.0000'), 3))
        self.assertEqual(ledger.verify_book(self.user, self.book['id'])['ok'], True)

    def test_a_processor_payout_is_one_entry_for_gross_sales_and_fees_and_must_put_the_net_in_the_bank(self):
        payout = self.rows['Processor payout']
        for lines in ([('1000', '1000', '0'), ('4000', '0', '1000')],                           # Gross booked to the bank.
                      [('1000', '940', '0'), ('6100', '30', '0'), ('4000', '0', '970')],         # Fees taken off the net a second time.
                      [('2000', '970', '0'), ('4000', '0', '970')]):                             # Bank account untouched.
            with self.assertRaises(ledger.LedgerError, msg=lines) as refused:
                self.entry(payout['date'], 'Processor payout', lines, payout, post=False)
            self.assertEqual(refused.exception.code, 'import_account_mismatch')
        self.assertEqual(Journal.objects.count(), 0)
        made = self.entry(payout['date'], 'Processor payout: sales 1,000.00 less fees 30.00',
                          [('1000', '970', '0'), ('6100', '30', '0'), ('4000', '0', '1000')], payout)
        self.assertEqual((made['debit'], self.state('Processor payout')), ('1000.0000', ('posted', 'entry')))
        report = ledger.financial_report(self.user, self.book['id'], {'from': '2026-03-01', 'to': '2026-03-31'})
        self.assertEqual((report['income_statement']['total_income'], report['income_statement']['total_expenses'], self.balance('1000')),
                         ('1000.0000', '30.0000', ('970.0000', '0.0000')))
