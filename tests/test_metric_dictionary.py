"""The metric dictionary: named figures that each say what they mean, what they were computed from, and which statement line they tie to."""
import json
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.ledger import metrics, services as ledger
from backend.maipa.ledger.models import Account, Journal
from backend.maipa.workspace.models import Installation, Membership
from tests.test_bookkeeping_posting import BookkeepingPostingTests

PERIOD = {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'}


class MetricDictionaryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='metric-owner')
        cls.other = get_user_model().objects.create_user(username='metric-other')
        Membership.objects.create(user=cls.user, installation=install, role='preparer')
        Membership.objects.create(user=cls.other, installation=install, role='administrator')
        cls.entity = Entity.objects.create(engagement=Engagement.objects.create(
            owner=cls.user, installation=install, name='Synthetic metrics'), name='Synthetic metric business')

    def setUp(self):
        self.book = ledger.create_book(self.user, self.entity.pk, PERIOD)
        ledger.create_account(self.user, self.book['id'], {'expected_revision': self.revision(), 'code': '1050', 'name': 'Tax savings', 'category': 'asset'})
        self.accounts = {a['code']: a['id'] for a in ledger.read_book(self.user, self.book['id'])['accounts']}
        ledger.set_account_cash(self.user, self.book['id'], self.accounts['1050'], {'expected_revision': self.revision(), 'cash': True})
        ledger.set_account_restricted(self.user, self.book['id'], self.accounts['1050'], {'expected_revision': self.revision(), 'restricted': True})
        self.entry('2026-01-15', 'January sale', (('1000', '3000', '0'), ('4000', '0', '3000')))
        self.entry('2026-02-10', 'February sale', (('1000', '5000', '0'), ('4000', '0', '5000')))
        self.entry('2026-02-20', 'Supplies', (('6200', '1200', '0'), ('1000', '0', '1200')))
        self.entry('2026-02-20', 'Set aside for tax', (('1050', '1000', '0'), ('1000', '0', '1000')))
        self.entry('2026-03-05', 'Rent', (('6200', '900', '0'), ('1000', '0', '900')))
        self.entry('2026-03-25', 'Supplier bill, unpaid', (('6200', '300', '0'), ('2000', '0', '300')))

    def revision(self):
        return ledger.read_book(self.user, self.book['id'])['revision']

    def entry(self, date, description, parts):
        made = ledger.propose_journal(self.user, self.book['id'], {
            'operation_id': str(uuid.uuid4()), 'expected_revision': self.revision(), 'date': date, 'description': description,
            'source': {'kind': 'manual', 'reference': f'synthetic-{uuid.uuid4()}'}, 'evidence': ['synthetic'],
            'lines': [{'account_id': self.accounts[code], 'debit': debit, 'credit': credit} for code, debit, credit in parts]})
        ledger.confirm_own_journal(self.user, self.book['id'], made['id'], {'expected_revision': self.revision(), 'payload_hash': made['payload_hash'], 'confirm': True})

    def read(self, start, end, user=None):
        value = metrics.dictionary(user or self.user, self.book['id'], {'from': start, 'to': end})
        return value, {item['key']: item for item in value['metrics']}

    def test_every_money_metric_ties_to_its_statement_line_and_shows_its_inputs(self):
        value, found = self.read('2026-02-01', '2026-03-31')
        report = ledger.financial_report(self.user, self.book['id'], {'from': '2026-02-01', 'to': '2026-03-31'})
        self.assertEqual((value['book_id'], value['currency'], value['from'], value['to'], value['days']), (self.book['id'], 'USD', '2026-02-01', '2026-03-31', 59))
        self.assertEqual([item['key'] for item in value['metrics']], ['revenue', 'expenses', 'net_income', 'net_margin', 'revenue_change', 'cash', 'usable_cash',
                                                                      'net_cash_change', 'cash_runway', 'total_assets', 'total_liabilities', 'net_worth'])
        ties = {'revenue': report['income_statement']['total_income'], 'expenses': report['income_statement']['total_expenses'], 'net_income': report['income_statement']['net_income'],
                'cash': report['cash_flow']['closing_cash'], 'net_cash_change': report['cash_flow']['net_change'],
                'total_assets': report['balance_sheet']['total_assets'], 'total_liabilities': report['balance_sheet']['total_liabilities']}
        self.assertEqual({key: found[key]['value'] for key in ties}, ties)
        self.assertEqual({key: found[key]['value'] for key in ties}, {'revenue': '5000.0000', 'expenses': '2400.0000', 'net_income': '2600.0000', 'cash': '5900.0000',
                                                                      'net_cash_change': '2900.0000', 'total_assets': '5900.0000', 'total_liabilities': '300.0000'})
        self.assertEqual(found['net_income'], {'key': 'net_income', 'name': 'Net income', 'unit': 'money', 'available': True, 'value': '2600.0000', 'reason': None,
            'definition': 'Revenue minus expenses for the period, from posted entries.', 'source': 'Income statement: net income',
            'inputs': [{'label': 'Revenue', 'value': '5000.0000'}, {'label': 'Expenses', 'value': '2400.0000'}]})
        self.assertEqual((found['net_margin']['value'], found['net_margin']['unit'], found['net_margin']['inputs']),
                         ('52.00', 'percent', [{'label': 'Net income', 'value': '2600.0000'}, {'label': 'Revenue', 'value': '5000.0000'}]))
        self.assertEqual((found['usable_cash']['value'], found['usable_cash']['inputs']),
                         ('4900.0000', [{'label': 'Cash in bank and cash accounts', 'value': '5900.0000'}, {'label': 'Set aside (restricted)', 'value': '1000.0000'}]))
        self.assertEqual((found['net_worth']['value'], found['net_worth']['inputs']),
                         ('5600.0000', [{'label': 'Total assets', 'value': '5900.0000'}, {'label': 'Total liabilities', 'value': '300.0000'}]))
        # Every metric explains itself; none is a bare number.
        for item in value['metrics']:
            self.assertEqual(set(item), {'key', 'name', 'unit', 'available', 'value', 'reason', 'definition', 'source', 'inputs'}, item['key'])
            self.assertTrue(item['name'] and item['definition'] and item['source'], item['key'])
            self.assertEqual((item['value'] is None, item['reason'] is not None), (not item['available'], not item['available']), item['key'])

    def test_metrics_that_cannot_be_computed_say_why_instead_of_guessing(self):
        _, wide = self.read('2026-02-01', '2026-03-31')
        self.assertEqual((wide['revenue_change']['available'], wide['revenue_change']['reason'], wide['revenue_change']['value']), (False, 'previous_period_outside_books', None))
        self.assertEqual((wide['cash_runway']['available'], wide['cash_runway']['reason']), (False, 'cash_not_decreasing'))
        _, march = self.read('2026-03-01', '2026-03-31')
        self.assertEqual((march['net_margin']['available'], march['net_margin']['reason'], march['revenue']['value']), (False, 'no_revenue', '0.0000'))
        ledger.set_account_restricted(self.user, self.book['id'], self.accounts['1050'], {'expected_revision': self.revision(), 'restricted': False})
        ledger.set_account_cash(self.user, self.book['id'], self.accounts['1050'], {'expected_revision': self.revision(), 'cash': False})
        ledger.set_account_cash(self.user, self.book['id'], self.accounts['1000'], {'expected_revision': self.revision(), 'cash': False})
        _, none = self.read('2026-03-01', '2026-03-31')
        self.assertEqual([(none[key]['available'], none[key]['reason']) for key in ('cash', 'usable_cash', 'net_cash_change', 'cash_runway')], [(False, 'no_cash_accounts')] * 4)
        self.assertEqual(none['expenses']['value'], '1200.0000')

    def test_changes_and_runway_use_exact_rounded_arithmetic_on_stated_inputs(self):
        _, february = self.read('2026-02-01', '2026-02-28')
        # 28 days against the 28 days before it: 5000 against 3000.
        self.assertEqual(february['revenue_change'], {'key': 'revenue_change', 'name': 'Revenue change', 'unit': 'percent', 'available': True, 'value': '66.67', 'reason': None,
            'definition': 'Revenue for the period compared with the period of the same length just before it, as a percentage of that earlier revenue.',
            'source': 'Income statement: total income, this period and 2026-01-04 to 2026-01-31',
            'inputs': [{'label': 'Revenue this period', 'value': '5000.0000'}, {'label': 'Revenue 2026-01-04 to 2026-01-31', 'value': '3000.0000'}]})
        _, march = self.read('2026-03-01', '2026-03-31')
        self.assertEqual((march['revenue_change']['value'], march['net_cash_change']['value'], march['usable_cash']['value']), ('-100.00', '-900.0000', '4900.0000'))
        # 900 out over 31 days is 203.2258 a week; 4900 lasts 24.1 weeks.
        self.assertEqual((march['cash_runway']['value'], march['cash_runway']['unit'], march['cash_runway']['inputs']),
                         ('24.1', 'weeks', [{'label': 'Usable cash', 'value': '4900.0000'}, {'label': 'Average weekly decrease in usable cash', 'value': '203.2258'}]))
        _, january = self.read('2026-01-01', '2026-01-31')
        self.assertEqual((january['revenue_change']['reason'], january['net_margin']['value']), ('previous_period_outside_books', '100.00'))
        # A previous period with no revenue gives no percentage.
        self.entry('2026-05-10', 'May sale', (('1000', '100', '0'), ('4000', '0', '100')))
        self.assertEqual(self.read('2026-05-01', '2026-05-31')[1]['revenue_change']['reason'], 'no_previous_revenue')

    def test_bad_ranges_and_other_people_are_refused_and_reading_changes_nothing(self):
        journals, revision = Journal.objects.count(), self.revision()
        for start, end in (('2025-12-01', '2026-01-31'), ('2026-03-01', '2027-01-31'), ('2026-04-01', '2026-03-01'), ('March', '2026-03-31'), ('2026-03-01', None)):
            with self.assertRaises(ledger.LedgerError, msg=(start, end)):
                self.read(start, end)
        with self.assertRaises(ledger.LedgerError):
            self.read('2026-02-01', '2026-03-31', self.other)
        for bad in ({}, {'from': '2026-02-01'}, {'from': '2026-02-01', 'to': '2026-03-31', 'keys': ['revenue']}):
            with self.assertRaises(ledger.LedgerError, msg=bad):
                metrics.dictionary(self.user, self.book['id'], bad)
        self.assertEqual((Journal.objects.count(), self.revision(), Account.objects.filter(restricted=True).count()), (journals, revision, 1))

    def test_the_agent_tool_returns_the_dictionary(self):
        broker, _, _ = BookkeepingPostingTests.broker(self)
        tool = lambda arguments: broker.call('maipa_books_metrics', {'book_id': self.book['id'], **arguments})
        self.assertEqual(json.loads(tool({'from': '2026-02-01', 'to': '2026-03-31'})['content'][0]['text']), self.read('2026-02-01', '2026-03-31')[0])
        for bad in ({}, {'from': '2026-02-01'}, {'from': '2026-02-01', 'to': '2026-03-31', 'metric': 'revenue'}, {'from': 'soon', 'to': '2026-03-31'}):
            self.assertTrue(tool(bad)['isError'], bad)
