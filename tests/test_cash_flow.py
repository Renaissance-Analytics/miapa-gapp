"""Cash flow: what moved the bank and cash accounts in a period, by the other account of each posted entry, exactly."""
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.ledger import services
from backend.maipa.workspace.models import Installation, Membership


class CashFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='cash-owner')
        cls.other = get_user_model().objects.create_user(username='cash-other')
        Membership.objects.create(user=cls.user, installation=install, role='preparer')
        Membership.objects.create(user=cls.other, installation=install, role='administrator')
        cls.entity = Entity.objects.create(engagement=Engagement.objects.create(
            owner=cls.user, installation=install, name='Synthetic cash flows'), name='Synthetic cash business')

    def setUp(self):
        self.book = services.create_book(self.user, self.entity.pk, {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'})
        self.accounts = {a['code']: a['id'] for a in self.book['accounts']}

    def revision(self):
        return services.read_book(self.user, self.book['id'])['revision']

    def account(self, code, name, category, cash=False):
        made = services.create_account(self.user, self.book['id'], {'expected_revision': self.revision(), 'code': code, 'name': name, 'category': category})
        self.accounts[code] = next(a['id'] for a in services.read_book(self.user, self.book['id'])['accounts'] if a['code'] == code)
        if cash:
            services.set_account_cash(self.user, self.book['id'], self.accounts[code], {'expected_revision': self.revision(), 'cash': True})
        return made

    def entry(self, day, lines, post=True):
        reference = f'synthetic-{uuid.uuid4()}'
        made = services.propose_journal(self.user, self.book['id'], {
            'operation_id': str(uuid.uuid4()), 'expected_revision': self.revision(), 'date': day, 'description': 'Synthetic entry',
            'source': {'kind': 'manual', 'reference': reference}, 'evidence': [reference],
            'lines': [{'account_id': self.accounts[code], 'debit': debit, 'credit': credit} for code, debit, credit in lines]})
        if post:
            services.confirm_own_journal(self.user, self.book['id'], made['id'],
                {'expected_revision': self.revision(), 'payload_hash': made['payload_hash'], 'confirm': True})
        return made

    def flow(self, start, end, user=None):
        return services.financial_report(user or self.user, self.book['id'], {'from': start, 'to': end})['cash_flow']

    def story(self):
        self.account('1010', 'Savings', 'asset', cash=True)
        self.entry('2026-01-05', [('1000', '5000', '0'), ('3000', '0', '5000')])          # Owner pays in.
        self.entry('2026-02-10', [('1000', '1250.5', '0'), ('4000', '0', '1250.5')])      # February sale, before the range.
        self.entry('2026-03-02', [('1010', '400', '0'), ('1000', '0', '400')])            # Transfer between own accounts.
        self.entry('2026-03-03', [('6200', '900', '0'), ('1000', '0', '900')])            # Paid expense.
        self.entry('2026-03-10', [('1500', '1200', '0'), ('6100', '0.1', '0'), ('1000', '0', '700.1'), ('2000', '0', '500')])  # Part paid, part owed.
        self.entry('2026-03-15', [('1000', '70', '0'), ('4000', '0', '70')])              # March sale.
        self.entry('2026-03-20', [('6200', '0.2', '0'), ('2000', '0', '0.2')])            # Unpaid bill: no cash.
        self.entry('2026-03-25', [('6200', '999', '0'), ('1000', '0', '999')], post=False)  # Draft is ignored.
        self.entry('2026-04-01', [('2000', '100', '0'), ('1010', '0', '100')])            # After the range.

    def test_cash_movements_are_attributed_exactly_to_the_other_accounts_of_each_posted_entry(self):
        self.story()
        march = self.flow('2026-03-01', '2026-03-31')
        self.assertEqual(set(march), {'accounts', 'opening_cash', 'closing_cash', 'net_change', 'received', 'paid', 'movements'})
        self.assertEqual([(a['code'], a['name'], a['opening'], a['closing']) for a in march['accounts']],
                         [('1000', 'Checking', '6250.5000', '4320.4000'), ('1010', 'Savings', '0.0000', '400.0000')])
        self.assertEqual((march['opening_cash'], march['closing_cash'], march['net_change']), ('6250.5000', '4720.4000', '-1530.1000'))
        # The purchase moved 700.10 of cash: equipment 1,200.00 and a 0.10 fee out, less the 500.00 still owed.
        self.assertEqual([(m['code'], m['category'], m['amount']) for m in march['movements']],
                         [('1500', 'asset', '-1200.0000'), ('2000', 'liability', '500.0000'), ('4000', 'income', '70.0000'),
                          ('6100', 'expense', '-0.1000'), ('6200', 'expense', '-900.0000')])
        self.assertEqual((march['received'], march['paid']), ('570.0000', '-2100.1000'))
        self.assertEqual(set(march['movements'][0]), {'id', 'code', 'name', 'category', 'cash', 'amount'})
        year = self.flow('2026-01-01', '2026-12-31')
        self.assertEqual((year['opening_cash'], year['closing_cash'], year['net_change']), ('0.0000', '4620.4000', '4620.4000'))
        self.assertEqual([(m['code'], m['amount']) for m in year['movements']],
                         [('1500', '-1200.0000'), ('2000', '400.0000'), ('3000', '5000.0000'), ('4000', '1320.5000'), ('6100', '-0.1000'), ('6200', '-900.0000')])

    def test_every_range_reconciles_opening_plus_movements_to_closing_and_to_the_balance_sheet(self):
        self.story()
        units = lambda value: int(value.replace('.', ''))
        for start, end in (('2026-01-01', '2026-01-31'), ('2026-02-01', '2026-03-09'), ('2026-03-10', '2026-03-10'), ('2026-03-11', '2026-12-31'), ('2026-01-01', '2026-12-31')):
            report = services.financial_report(self.user, self.book['id'], {'from': start, 'to': end})
            flow = report['cash_flow']
            moved = sum(units(m['amount']) for m in flow['movements'])
            self.assertEqual(moved, units(flow['net_change']), (start, end))
            self.assertEqual(units(flow['opening_cash']) + moved, units(flow['closing_cash']), (start, end))
            self.assertEqual(units(flow['received']) + units(flow['paid']), moved, (start, end))
            self.assertEqual(sum(units(a['closing']) for a in flow['accounts']), units(flow['closing_cash']), (start, end))
            on_sheet = sum(units(a['amount']) for a in report['balance_sheet']['assets'] if a['cash'])
            self.assertEqual(on_sheet, units(flow['closing_cash']), (start, end))
        again = services.financial_report(self.user, self.book['id'], {'from': '2026-03-01', 'to': '2026-03-31'})
        self.assertEqual(again, services.financial_report(self.user, self.book['id'], {'from': '2026-03-01', 'to': '2026-03-31'}))

    def test_reversals_cancel_and_books_without_a_marked_account_report_no_cash(self):
        paid = self.entry('2026-03-03', [('6200', '900', '0'), ('1000', '0', '900')])
        services.reverse_journal(self.user, self.book['id'], paid['id'], {'expected_revision': self.revision(), 'date': '2026-03-04'})
        march = self.flow('2026-03-01', '2026-03-31')
        self.assertEqual((march['movements'], march['net_change'], march['received'], march['paid']), ([], '0.0000', '0.0000', '0.0000'))
        self.assertEqual((self.flow('2026-03-03', '2026-03-03')['net_change'], self.flow('2026-03-04', '2026-03-04')['net_change']), ('-900.0000', '900.0000'))
        services.set_account_cash(self.user, self.book['id'], self.accounts['1000'], {'expected_revision': self.revision(), 'cash': False})
        self.entry('2026-03-15', [('1000', '70', '0'), ('4000', '0', '70')])
        self.assertEqual(self.flow('2026-03-01', '2026-03-31'),
                         {'accounts': [], 'opening_cash': '0.0000', 'closing_cash': '0.0000', 'net_change': '0.0000', 'received': '0.0000', 'paid': '0.0000', 'movements': []})

    def test_comparisons_keep_their_shape_and_foreign_readers_and_bad_ranges_are_refused(self):
        self.story()
        both = services.financial_report(self.user, self.book['id'], {'from': '2026-03-01', 'to': '2026-03-31', 'compare_from': '2026-02-01', 'compare_to': '2026-02-28'})
        self.assertEqual(both['cash_flow'], self.flow('2026-03-01', '2026-03-31'))
        self.assertEqual(set(both['comparison']), {'from', 'to', 'income_statement', 'balance_sheet', 'change'})
        for start, end in (('2026-03-31', '2026-03-01'), ('2025-12-31', '2026-03-01'), ('March', '2026-03-31')):
            with self.assertRaises(services.LedgerError):
                self.flow(start, end)
        with self.assertRaises(services.LedgerError):
            self.flow('2026-03-01', '2026-03-31', self.other)

    def test_http_and_the_agent_tool_return_the_same_cash_flow(self):
        import json
        from tests.test_bookkeeping_posting import BookkeepingPostingTests
        self.story()
        self.client.force_login(self.user)
        response = self.client.get(f"/api/books/{self.book['id']}/report/?from=2026-03-01&to=2026-03-31", secure=True, HTTP_X_MAIPA_AGENT_ACTOR=str(self.user.pk))
        self.assertEqual((response.status_code, response.json()['cash_flow']), (200, self.flow('2026-03-01', '2026-03-31')))
        broker, _, _ = BookkeepingPostingTests.broker(self)
        answer = broker.call('maipa_books_report', {'book_id': self.book['id'], 'from': '2026-03-01', 'to': '2026-03-31'})
        self.assertEqual(json.loads(answer['content'][0]['text'])['cash_flow']['net_change'], '-1530.1000')
