"""Budgets and variance: a monthly budget the person approved, compared with what the books actually show, with nothing treated as zero by silence."""
import json
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.maipa.approvals import services as approvals
from backend.maipa.approvals.models import ActionRequest
from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.ledger import budgets, services as ledger
from backend.maipa.ledger.models import BudgetLine, Journal
from backend.maipa.workspace.models import Installation, Membership
from tests.test_bookkeeping_posting import BookkeepingPostingTests

PERIOD = {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'}


class BudgetVarianceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='budget-owner')
        cls.other = get_user_model().objects.create_user(username='budget-other')
        Membership.objects.create(user=cls.user, installation=install, role='preparer')
        Membership.objects.create(user=cls.other, installation=install, role='administrator')
        cls.entity = Entity.objects.create(engagement=Engagement.objects.create(
            owner=cls.user, installation=install, name='Synthetic budgets'), name='Synthetic budget business')

    def setUp(self):
        self.book = ledger.create_book(self.user, self.entity.pk, PERIOD)
        ledger.create_account(self.user, self.book['id'], {'expected_revision': self.revision(), 'code': '6300', 'name': 'Rent', 'category': 'expense'})
        self.accounts = {a['code']: a['id'] for a in ledger.read_book(self.user, self.book['id'])['accounts']}
        self.entry('2026-01-15', 'January sale', (('1000', '3000', '0'), ('4000', '0', '3000')))
        self.entry('2026-02-10', 'February sale', (('1000', '5000', '0'), ('4000', '0', '5000')))
        self.entry('2026-02-20', 'Supplies', (('6200', '1200', '0'), ('1000', '0', '1200')))
        self.entry('2026-03-05', 'More supplies', (('6200', '900', '0'), ('1000', '0', '900')))
        self.entry('2026-03-25', 'Supplier bill, unpaid', (('6200', '300', '0'), ('2000', '0', '300')))
        self.entry('2026-03-28', 'Rent', (('6300', '400', '0'), ('1000', '0', '400')))

    def revision(self):
        return ledger.read_book(self.user, self.book['id'])['revision']

    def entry(self, date, description, parts):
        made = ledger.propose_journal(self.user, self.book['id'], {
            'operation_id': str(uuid.uuid4()), 'expected_revision': self.revision(), 'date': date, 'description': description,
            'source': {'kind': 'manual', 'reference': f'synthetic-{uuid.uuid4()}'}, 'evidence': ['synthetic'],
            'lines': [{'account_id': self.accounts[code], 'debit': debit, 'credit': credit} for code, debit, credit in parts]})
        ledger.confirm_own_journal(self.user, self.book['id'], made['id'], {'expected_revision': self.revision(), 'payload_hash': made['payload_hash'], 'confirm': True})

    def ask(self, user=None, **changes):
        payload = {'book_id': self.book['id'], 'month': '2026-02', 'through': '2026-03',
                   'lines': [{'account_id': self.accounts['4000'], 'amount': '4500'}, {'account_id': self.accounts['6200'], 'amount': '1000'}], **changes}
        return approvals.request_action(user or self.user, {'operation': 'budget.set', 'summary': 'Set the February and March budget', 'payload': payload})

    def approve(self, item):
        return approvals.decide(self.user, item['id'], {'decision': 'approve', 'payload_hash': item['payload_hash']})

    def variance(self, start='2026-02', end='2026-03', user=None):
        value = budgets.variance(user or self.user, self.book['id'], {'from': start, 'to': end})
        return value, {row['code']: row for row in value['rows']}

    def test_an_approved_budget_is_compared_with_the_books_account_by_account(self):
        empty, _ = self.variance()
        self.assertEqual((empty['budgeted'], [row['budget'] for row in empty['rows']]), (False, [None, None, None]))
        item = self.ask()
        self.assertEqual(item['detail'], {'books': 'Synthetic budget business', 'currency': 'USD', 'month': '2026-02', 'through': '2026-03', 'months': 2, 'current': True,
            'lines': [{'account': '4000 Sales', 'category': 'income', 'amount': '4500.0000'}, {'account': '6200 Office expenses', 'category': 'expense', 'amount': '1000.0000'}]})
        self.assertEqual(BudgetLine.objects.count(), 0)
        journals, revision = Journal.objects.count(), self.revision()
        done = self.approve(item)
        self.assertEqual((done['state'], done['result']), ('approved', {'months': 2, 'lines': 4}))
        value, rows = self.variance()
        self.assertEqual((value['from'], value['to'], value['start'], value['end'], value['months'], value['budgeted'], value['currency']),
                         ('2026-02', '2026-03', '2026-02-01', '2026-03-31', 2, True, 'USD'))
        self.assertEqual(rows['4000'], {'code': '4000', 'name': 'Sales', 'category': 'income', 'budget': '9000.0000', 'budget_months': 2, 'actual': '5000.0000',
                                        'variance': '-4000.0000', 'percent': '-44.44', 'favourable': False})
        self.assertEqual(rows['6200'], {'code': '6200', 'name': 'Office expenses', 'category': 'expense', 'budget': '2000.0000', 'budget_months': 2, 'actual': '2400.0000',
                                        'variance': '400.0000', 'percent': '20.00', 'favourable': False})
        # An account with spending but no budget is shown as unbudgeted, never as a budget of zero.
        self.assertEqual(rows['6300'], {'code': '6300', 'name': 'Rent', 'category': 'expense', 'budget': None, 'budget_months': 0, 'actual': '400.0000',
                                        'variance': None, 'percent': None, 'favourable': None})
        self.assertEqual(value['totals'], {'income': {'budget': '9000.0000', 'actual': '5000.0000', 'variance': '-4000.0000'},
                                           'expenses': {'budget': '2000.0000', 'actual': '2400.0000', 'variance': '400.0000'},
                                           'unbudgeted_income': '0.0000', 'unbudgeted_expenses': '400.0000', 'net_budget': '7000.0000', 'net_actual': '2200.0000'})
        report = ledger.financial_report(self.user, self.book['id'], {'from': '2026-02-01', 'to': '2026-03-31'})
        self.assertEqual(value['totals']['net_actual'], report['income_statement']['net_income'])
        # A budget is not an entry: no journal, no book revision, and approving again changes nothing.
        self.assertEqual((Journal.objects.count(), self.revision(), self.approve(item)['result'], BudgetLine.objects.count()), (journals, revision, done['result'], 4))

    def test_months_without_a_budget_are_counted_and_a_better_result_is_marked_favourable(self):
        self.approve(self.ask())
        value, rows = self.variance('2026-01', '2026-03')
        self.assertEqual((value['months'], rows['4000']['budget'], rows['4000']['budget_months'], rows['4000']['actual'], rows['4000']['variance']), (3, '9000.0000', 2, '8000.0000', '-1000.0000'))
        # Setting one account again replaces only that account for those months.
        self.approve(self.ask(month='2026-02', through='2026-02', lines=[{'account_id': self.accounts['6200'], 'amount': '1500'}, {'account_id': self.accounts['6300'], 'amount': '0'}]))
        _, february = self.variance('2026-02', '2026-02')
        self.assertEqual((february['6200']['budget'], february['6200']['variance'], february['6200']['favourable'], february['6200']['percent']), ('1500.0000', '-300.0000', True, '-20.00'))
        self.assertEqual((february['4000']['budget'], february['4000']['favourable'], february['4000']['percent']), ('4500.0000', True, '11.11'))
        # A budget of zero is a real budget; its percentage cannot be computed.
        self.assertEqual(february['6300'], {'code': '6300', 'name': 'Rent', 'category': 'expense', 'budget': '0.0000', 'budget_months': 1, 'actual': '0.0000',
                                            'variance': '0.0000', 'percent': None, 'favourable': True})
        self.assertEqual(BudgetLine.objects.count(), 5)

    def test_a_budget_is_cleared_only_by_approval(self):
        self.approve(self.ask())
        stop = approvals.request_action(self.user, {'operation': 'budget.clear', 'summary': 'Drop the March budget', 'payload': {'book_id': self.book['id'], 'month': '2026-03'}})
        self.assertEqual(stop['detail'], {'books': 'Synthetic budget business', 'month': '2026-03', 'through': '2026-03', 'months': 1, 'lines': 2, 'current': True})
        self.assertEqual(self.variance()[1]['4000']['budget'], '9000.0000')                                  # Still set until approved.
        done = self.approve(stop)
        self.assertEqual((done['state'], done['result']), ('approved', {'months': 1, 'lines': 2}))
        self.assertEqual((self.variance()[1]['4000']['budget'], self.variance()[1]['4000']['budget_months'], BudgetLine.objects.count()), ('4500.0000', 1, 2))
        with self.assertRaises(approvals.ApprovalError) as refused:
            approvals.request_action(self.user, {'operation': 'budget.clear', 'summary': 'Drop it again', 'payload': {'book_id': self.book['id'], 'month': '2026-03'}})
        self.assertEqual(refused.exception.code, 'budget_nothing_to_clear')

    def test_budgets_for_other_account_kinds_bad_months_and_other_people_are_refused_before_anything_is_saved(self):
        line = lambda code, amount='100': {'account_id': self.accounts[code], 'amount': amount}
        cases = ((dict(lines=[line('1000')]), 'budget_account_not_income_or_expense'), (dict(lines=[line('2000')]), 'budget_account_not_income_or_expense'),
                 (dict(lines=[line('6200'), line('6200')]), 'budget_account_repeated'), (dict(lines=[{'account_id': str(uuid.uuid4()), 'amount': '100'}]), 'ledger_account_not_found'),
                 (dict(lines=[line('6200', '-5')]), None), (dict(lines=[line('6200', 100)]), None), (dict(lines=[]), None), (dict(lines='all'), None),
                 (dict(lines=[{'account_id': self.accounts['6200'], 'amount': '1', 'note': 'x'}]), None), (dict(month='2025-12'), 'ledger_date_outside_period'),
                 (dict(through='2027-01'), 'ledger_date_outside_period'), (dict(month='2026-04', through='2026-03'), 'budget_range_reversed'), (dict(month='2026-2'), None),
                 (dict(month='February'), None), (dict(force=True), None))
        for changes, code in cases:
            with self.assertRaises(approvals.ApprovalError, msg=changes) as refused:
                self.ask(**changes)
            if code:
                self.assertEqual(refused.exception.code, code, changes)
        with self.assertRaises(approvals.ApprovalError):
            self.ask(self.other)
        for start, end in (('2025-12', '2026-03'), ('2026-03', '2027-01'), ('2026-04', '2026-03'), ('March', '2026-03'), ('2026-03', None)):
            with self.assertRaises(ledger.LedgerError, msg=(start, end)):
                self.variance(start, end)
        with self.assertRaises(ledger.LedgerError):
            self.variance(user=self.other)
        with self.assertRaises(ledger.LedgerError):
            budgets.variance(self.user, self.book['id'], {'from': '2026-02', 'to': '2026-03', 'accounts': []})
        self.assertEqual((ActionRequest.objects.count(), BudgetLine.objects.count()), (0, 0))

    def test_the_agent_tool_reads_the_variance_and_cannot_set_a_budget(self):
        self.approve(self.ask())
        broker, _, _ = BookkeepingPostingTests.broker(self)
        tool = lambda arguments: broker.call('maipa_books_budget', {'book_id': self.book['id'], **arguments})
        self.assertEqual(json.loads(tool({'from': '2026-02', 'to': '2026-03'})['content'][0]['text']), self.variance()[0])
        for bad in ({}, {'from': '2026-02'}, {'from': '2026-02', 'to': '2026-03', 'lines': []}, {'from': 'soon', 'to': '2026-03'}):
            self.assertTrue(tool(bad)['isError'], bad)
        self.assertEqual(BudgetLine.objects.count(), 4)
