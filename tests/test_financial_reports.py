"""Income statement and balance sheet from posted entries only, exact and balanced."""
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.ledger import services
from backend.maipa.workspace.models import Installation, Membership


class FinancialReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='report-owner')
        cls.other = get_user_model().objects.create_user(username='report-other')
        Membership.objects.create(user=cls.user, installation=install, role='preparer')
        Membership.objects.create(user=cls.other, installation=install, role='administrator')
        cls.entity = Entity.objects.create(engagement=Engagement.objects.create(
            owner=cls.user, installation=install, name='Synthetic reports'), name='Synthetic report business')

    def setUp(self):
        self.book = services.create_book(self.user, self.entity.pk, {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'})
        self.accounts = {a['code']: a['id'] for a in self.book['accounts']}

    def entry(self, day, debit, credit, amount, post=True):
        revision = services.read_book(self.user, self.book['id'])['revision']
        reference = f'synthetic-{uuid.uuid4()}'
        made = services.propose_journal(self.user, self.book['id'], {
            'operation_id': str(uuid.uuid4()), 'expected_revision': revision, 'date': day, 'description': 'Synthetic entry',
            'source': {'kind': 'manual', 'reference': reference}, 'evidence': [reference],
            'lines': [{'account_id': self.accounts[debit], 'debit': amount, 'credit': '0'}, {'account_id': self.accounts[credit], 'debit': '0', 'credit': amount}]})
        if post:
            services.confirm_own_journal(self.user, self.book['id'], made['id'],
                {'expected_revision': revision + 1, 'payload_hash': made['payload_hash'], 'confirm': True})

    def report(self, start, end):
        return services.financial_report(self.user, self.book['id'], {'from': start, 'to': end})

    def test_income_statement_covers_only_the_range_and_balance_sheet_balances_as_of_the_end_date(self):
        self.entry('2026-01-05', '1000', '3000', '5000')        # Owner contribution.
        self.entry('2026-02-10', '1000', '4000', '1250.5')      # February sale.
        self.entry('2026-03-03', '6200', '1000', '900')         # March expense.
        self.entry('2026-03-05', '6100', '1000', '0.1')
        self.entry('2026-03-20', '6200', '2000', '0.2')         # Unpaid bill.
        self.entry('2026-04-01', '1000', '4000', '70')          # After the range.
        self.entry('2026-03-25', '6200', '1000', '999', post=False)  # Draft is ignored.
        march = self.report('2026-03-01', '2026-03-31')
        self.assertEqual((march['from'], march['to'], march['currency']), ('2026-03-01', '2026-03-31', 'USD'))
        self.assertEqual(march['income_statement']['income'], [])
        self.assertEqual([(r['code'], r['amount']) for r in march['income_statement']['expenses']], [('6100', '0.1000'), ('6200', '900.2000')])
        self.assertEqual((march['income_statement']['total_income'], march['income_statement']['total_expenses'], march['income_statement']['net_income']),
                         ('0.0000', '900.3000', '-900.3000'))
        sheet = march['balance_sheet']
        self.assertEqual([(r['code'], r['amount']) for r in sheet['assets']], [('1000', '5350.4000')])
        self.assertEqual([(r['code'], r['amount']) for r in sheet['liabilities']], [('2000', '0.2000')])
        self.assertEqual([(r['code'], r['amount']) for r in sheet['equity']], [('3000', '5000.0000')])
        self.assertEqual((sheet['total_assets'], sheet['total_liabilities'], sheet['total_equity'], sheet['earnings_to_date']),
                         ('5350.4000', '0.2000', '5000.0000', '350.2000'))
        self.assertEqual(sheet['total_liabilities_and_equity'], '5350.4000')
        year = self.report('2026-01-01', '2026-12-31')
        self.assertEqual((year['income_statement']['total_income'], year['income_statement']['net_income']), ('1320.5000', '420.2000'))
        self.assertEqual(year['balance_sheet']['total_assets'], year['balance_sheet']['total_liabilities_and_equity'])
        self.assertEqual(year['pending_count'], 1)

    def test_empty_books_report_zero_and_invalid_ranges_or_foreign_readers_are_refused(self):
        empty = self.report('2026-01-01', '2026-01-31')
        self.assertEqual((empty['income_statement']['net_income'], empty['balance_sheet']['total_assets'], empty['balance_sheet']['total_liabilities_and_equity']),
                         ('0.0000', '0.0000', '0.0000'))
        for value in ({'from': '2026-02-01', 'to': '2026-01-31'}, {'from': '2025-12-31', 'to': '2026-01-31'}, {'from': '2026-01-01', 'to': '2027-01-01'},
                      {'from': 'January', 'to': '2026-01-31'}, {'from': '2026-01-01'}, {'from': '2026-01-01', 'to': '2026-01-31', 'account': 'x'}):
            with self.assertRaises(services.LedgerError):
                services.financial_report(self.user, self.book['id'], value)
        with self.assertRaises(services.LedgerError):
            services.financial_report(self.other, self.book['id'], {'from': '2026-01-01', 'to': '2026-01-31'})

    def compare(self, start, end, other_start, other_end, user=None):
        return services.financial_report(user or self.user, self.book['id'],
            {'from': start, 'to': end, 'compare_from': other_start, 'compare_to': other_end})

    def test_comparison_reports_both_periods_and_exact_differences(self):
        self.entry('2026-01-05', '1000', '3000', '5000')
        self.entry('2026-02-10', '1000', '4000', '1250.5')
        self.entry('2026-03-03', '6200', '1000', '900')
        self.entry('2026-03-05', '6100', '1000', '0.1')
        self.entry('2026-03-20', '6200', '2000', '0.2')
        self.entry('2026-02-20', '6200', '1000', '999', post=False)
        both = self.compare('2026-03-01', '2026-03-31', '2026-02-01', '2026-02-28')
        march, february = self.report('2026-03-01', '2026-03-31'), self.report('2026-02-01', '2026-02-28')
        self.assertEqual({key: both[key] for key in march}, march)
        other = both['comparison']
        self.assertEqual(set(other), {'from', 'to', 'income_statement', 'balance_sheet', 'cash_flow', 'change'})
        self.assertEqual(other['cash_flow'], february['cash_flow'])
        self.assertEqual((other['from'], other['to']), ('2026-02-01', '2026-02-28'))
        self.assertEqual((other['income_statement'], other['balance_sheet']), (february['income_statement'], february['balance_sheet']))
        self.assertEqual((other['income_statement']['net_income'], other['balance_sheet']['total_assets']), ('1250.5000', '6250.5000'))
        self.assertEqual(other['change'], {'total_income': '-1250.5000', 'total_expenses': '900.3000', 'net_income': '-2150.8000',
            'total_assets': '-900.1000', 'total_liabilities': '0.2000', 'total_equity': '0.0000', 'earnings_to_date': '-900.3000',
            'net_cash_change': '-2150.6000'})
        same = self.compare('2026-03-01', '2026-03-31', '2026-03-01', '2026-03-31')['comparison']['change']
        self.assertEqual(set(same.values()), {'0.0000'})

    def test_incomplete_or_out_of_period_comparisons_and_foreign_readers_are_refused(self):
        for value in ({'compare_from': '2026-02-01'}, {'compare_to': '2026-02-28'}, {'compare_from': '2026-02-28', 'compare_to': '2026-02-01'},
                      {'compare_from': '2025-12-01', 'compare_to': '2025-12-31'}, {'compare_from': '2026-12-01', 'compare_to': '2027-01-01'},
                      {'compare_from': 'last month', 'compare_to': '2026-02-28'}, {'compare_from': None, 'compare_to': None},
                      {'compare_from': '2026-02-01', 'compare_to': '2026-02-28', 'basis': 'cash'}):
            with self.assertRaises(services.LedgerError):
                services.financial_report(self.user, self.book['id'], {'from': '2026-03-01', 'to': '2026-03-31', **value})
        with self.assertRaises(services.LedgerError):
            self.compare('2026-03-01', '2026-03-31', '2026-02-01', '2026-02-28', self.other)

    def test_http_comparison_matches_the_service_and_refuses_half_a_comparison(self):
        self.entry('2026-02-10', '1000', '4000', '10')
        url = f"/api/books/{self.book['id']}/report/?from=2026-03-01&to=2026-03-31&compare_from=2026-02-01"
        self.client.force_login(self.user)
        actor = {'HTTP_X_MAIPA_AGENT_ACTOR': str(self.user.pk)}
        self.assertEqual(self.client.get(url, secure=True, **actor).status_code, 400)
        response = self.client.get(url + '&compare_to=2026-02-28', secure=True, **actor)
        self.assertEqual((response.status_code, response.json()), (200, self.compare('2026-03-01', '2026-03-31', '2026-02-01', '2026-02-28')))
        self.assertEqual(response.json()['comparison']['change']['net_income'], '-10.0000')
        self.assertEqual(self.client.get(url + '&compare_to=2026-02-28&extra=1', secure=True, **actor).status_code, 400)

    def test_http_report_is_owner_bound_and_matches_the_service(self):
        self.entry('2026-02-10', '1000', '4000', '10')
        url = f"/api/books/{self.book['id']}/report/?from=2026-01-01&to=2026-12-31"
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(url, secure=True).status_code, 409)
        response = self.client.get(url, secure=True, HTTP_X_MAIPA_AGENT_ACTOR=str(self.user.pk))
        self.assertEqual((response.status_code, response.json()), (200, self.report('2026-01-01', '2026-12-31')))
        self.assertEqual(self.client.get(url.replace('to=2026-12-31', 'to=soon'), secure=True, HTTP_X_MAIPA_AGENT_ACTOR=str(self.user.pk)).status_code, 400)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url, secure=True, HTTP_X_MAIPA_AGENT_ACTOR=str(self.other.pk)).status_code, 404)
