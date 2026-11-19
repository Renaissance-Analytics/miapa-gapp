"""Opening balances: one approved, balanced starting entry that reconciles to the supplied totals."""
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.maipa.approvals import services as approvals
from backend.maipa.approvals.models import ActionRequest
from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.ledger import services as ledger
from backend.maipa.ledger.models import Journal
from backend.maipa.workspace.models import Installation, Membership

PERIOD = {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'}


class OpeningBalanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='opening-owner')
        cls.other = get_user_model().objects.create_user(username='opening-other')
        Membership.objects.create(user=cls.user, installation=install, role='preparer')
        Membership.objects.create(user=cls.other, installation=install, role='administrator')
        engagement = Engagement.objects.create(owner=cls.user, installation=install, name='Synthetic opening')
        cls.entity = Entity.objects.create(engagement=engagement, name='Synthetic opening business')
        cls.second = Entity.objects.create(engagement=engagement, name='Synthetic other business')

    def setUp(self):
        self.book = ledger.create_book(self.user, self.entity.pk, PERIOD)
        self.accounts = {a['code']: a['id'] for a in self.book['accounts']}

    def line(self, code, debit='0', credit='0'):
        return {'account_id': self.accounts[code], 'debit': debit, 'credit': credit}

    def payload(self, balances=None, control=None):
        return {'book_id': self.book['id'],
                'balances': balances if balances is not None else [self.line('1000', debit='5000'), self.line('1500', debit='1200.5'), self.line('2000', credit='700.25')],
                'control': control if control is not None else {'debit': '6200.5', 'credit': '700.25'}}

    def ask(self, user=None, **changes):
        return approvals.request_action(user or self.user, {'operation': 'opening.balance', 'summary': 'Record opening balances',
                                                            'payload': self.payload(**changes)})

    def approve(self, item):
        return approvals.decide(self.user, item['id'], {'decision': 'approve', 'payload_hash': item['payload_hash']})

    def test_approved_opening_posts_one_balanced_entry_at_period_start_with_the_difference_in_opening_equity(self):
        item = self.ask()
        self.assertEqual(Journal.objects.count(), 0)
        self.assertEqual(item['detail'], {'books': 'Synthetic opening business', 'currency': 'USD', 'date': '2026-01-01', 'current': True,
            'total_debit': '6200.5000', 'total_credit': '6200.5000', 'control_debit': '6200.5000', 'control_credit': '700.2500',
            'lines': [{'account': '1000 Checking', 'debit': '5000.0000', 'credit': '0.0000', 'offset': False},
                      {'account': '1500 Equipment', 'debit': '1200.5000', 'credit': '0.0000', 'offset': False},
                      {'account': '2000 Accounts payable', 'debit': '0.0000', 'credit': '700.2500', 'offset': False},
                      {'account': '3900 Opening balance equity', 'debit': '0.0000', 'credit': '5500.2500', 'offset': True}]})
        done = self.approve(item)
        self.assertEqual((done['state'], done['result']['state']), ('approved', 'posted'))
        entry = Journal.objects.get()
        self.assertEqual((entry.state, entry.source_kind, entry.source_reference, entry.date.isoformat(), entry.total_units),
                         ('posted', 'opening', 'opening-1', '2026-01-01', 62005000))
        report = ledger.financial_report(self.user, self.book['id'], {'from': '2026-01-01', 'to': '2026-01-01'})
        sheet = report['balance_sheet']
        self.assertEqual((sheet['total_assets'], sheet['total_liabilities'], sheet['total_equity'], sheet['total_liabilities_and_equity']),
                         ('6200.5000', '700.2500', '5500.2500', '6200.5000'))
        self.assertEqual(report['income_statement']['net_income'], '0.0000')
        check = ledger.verify_book(self.user, self.book['id'])
        self.assertEqual((check['ok'], check['posted_count'], check['posted_debit']), (True, 1, '6200.5000'))
        self.assertEqual(self.approve(item)['result'], done['result'])
        self.assertEqual(Journal.objects.count(), 1)

    def test_totals_that_do_not_reconcile_and_malformed_balances_are_refused_and_not_recorded(self):
        foreign = ledger.create_book(self.user, self.second.pk, PERIOD)['accounts'][0]['id']
        good = self.payload()['balances']
        for changes in ({'control': {'debit': '6200.5', 'credit': '700'}}, {'control': {'debit': '6200', 'credit': '700.25'}},
                        {'control': {'debit': '6200.5'}}, {'control': {'debit': '6200.5', 'credit': '700.25', 'net': '5500.25'}},
                        {'balances': []}, {'balances': good + [self.line('1000', debit='1')], 'control': {'debit': '6201.5', 'credit': '700.25'}},
                        {'balances': [self.line('3900', credit='10'), self.line('1000', debit='10')], 'control': {'debit': '10', 'credit': '10'}},
                        {'balances': [self.line('1000', debit='10', credit='10')], 'control': {'debit': '10', 'credit': '10'}},
                        {'balances': [self.line('1000')], 'control': {'debit': '0', 'credit': '0'}},
                        {'balances': [self.line('1000', debit='-5')], 'control': {'debit': '-5', 'credit': '0'}},
                        {'balances': [self.line('1000', debit='1.00001')], 'control': {'debit': '1.00001', 'credit': '0'}},
                        {'balances': [{'account_id': foreign, 'debit': '10', 'credit': '0'}], 'control': {'debit': '10', 'credit': '0'}},
                        {'balances': [{'account_id': str(uuid.uuid4()), 'debit': '10', 'credit': '0'}], 'control': {'debit': '10', 'credit': '0'}},
                        {'balances': [{**self.line('1000', debit='10'), 'memo': 'x'}], 'control': {'debit': '10', 'credit': '0'}}):
            with self.assertRaises(approvals.ApprovalError, msg=changes):
                self.ask(**changes)
        with self.assertRaises(approvals.ApprovalError):
            self.ask(self.other)
        self.assertEqual((ActionRequest.objects.count(), Journal.objects.count()), (0, 0))

    def test_a_second_opening_is_refused_until_the_first_is_reversed_and_balanced_figures_need_no_offset(self):
        first = self.approve(self.ask())
        with self.assertRaises(approvals.ApprovalError) as refused:
            self.ask()
        self.assertEqual(refused.exception.code, 'ledger_opening_exists')
        revision = ledger.read_book(self.user, self.book['id'])['revision']
        ledger.reverse_journal(self.user, self.book['id'], first['result']['journal_id'], {'expected_revision': revision, 'date': '2026-01-01'})
        balanced = [self.line('1000', debit='300'), self.line('3000', credit='300')]
        item = self.ask(balances=balanced, control={'debit': '300', 'credit': '300'})
        self.assertEqual([line['offset'] for line in item['detail']['lines']], [False, False])
        self.approve(item)
        latest = Journal.objects.order_by('-sequence').first()
        self.assertEqual((latest.source_kind, latest.source_reference, latest.total_units), ('opening', 'opening-2', 3000000))
        report = ledger.journal_preview(self.user, self.book['id'])
        self.assertEqual((report['trial_debit'], report['trial_credit']), ('300.0000', '300.0000'))
        self.assertEqual(ledger.verify_book(self.user, self.book['id'])['ok'], True)

    def test_a_stale_request_fails_without_posting_and_a_closed_first_day_refuses_new_openings(self):
        item = self.ask()
        other = self.ask(balances=[self.line('1000', debit='10')], control={'debit': '10', 'credit': '0'})
        self.approve(other)
        stale = self.approve(item)
        self.assertEqual((stale['state'], stale['error']), ('failed', 'ledger_opening_exists'))
        self.assertEqual(Journal.objects.count(), 1)
        revision = ledger.read_book(self.user, self.book['id'])['revision']
        ledger.reverse_journal(self.user, self.book['id'], str(Journal.objects.get().pk), {'expected_revision': revision, 'date': '2026-01-01'})
        ledger.change_period(self.user, self.book['id'], {'expected_revision': revision + 2, 'action': 'close', 'through': '2026-01-31'})
        with self.assertRaises(approvals.ApprovalError) as refused:
            self.ask()
        self.assertEqual(refused.exception.code, 'ledger_period_closed')

    def test_ordinary_entries_cannot_claim_to_be_an_opening_entry(self):
        with self.assertRaises(ledger.LedgerError):
            ledger.propose_journal(self.user, self.book['id'], {
                'operation_id': str(uuid.uuid4()), 'expected_revision': 0, 'date': '2026-01-01', 'description': 'Synthetic opening',
                'source': {'kind': 'opening', 'reference': 'opening-1'}, 'evidence': ['synthetic'],
                'lines': [self.line('1000', debit='10'), self.line('3900', credit='10')]})
        self.assertEqual(Journal.objects.count(), 0)
