"""Usable cash and a 13-week forecast built only from what the books already know: posted, draft and recurring entries."""
import json
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.maipa.approvals import services as approvals
from backend.maipa.approvals.models import ActionRequest
from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.ledger import services as ledger
from backend.maipa.ledger.models import Account, Journal, LedgerEvent
from backend.maipa.workspace.models import Installation, Membership
from tests.test_bookkeeping_posting import BookkeepingPostingTests

PERIOD = {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'}


class CashForecastTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='forecast-owner')
        cls.other = get_user_model().objects.create_user(username='forecast-other')
        Membership.objects.create(user=cls.user, installation=install, role='preparer')
        Membership.objects.create(user=cls.other, installation=install, role='administrator')
        cls.entity = Entity.objects.create(engagement=Engagement.objects.create(
            owner=cls.user, installation=install, name='Synthetic forecasts'), name='Synthetic forecast business')

    def setUp(self):
        self.book = ledger.create_book(self.user, self.entity.pk, PERIOD)
        ledger.create_account(self.user, self.book['id'], {'expected_revision': self.revision(), 'code': '1050', 'name': 'Tax savings', 'category': 'asset'})
        self.accounts = {a['code']: a['id'] for a in ledger.read_book(self.user, self.book['id'])['accounts']}
        ledger.set_account_cash(self.user, self.book['id'], self.accounts['1050'], {'expected_revision': self.revision(), 'cash': True})
        self.entry('2026-02-10', 'Client payment', (('1000', '5000', '0'), ('4000', '0', '5000')))
        self.entry('2026-02-20', 'Set aside for tax', (('1050', '1000', '0'), ('1000', '0', '1000')))

    def revision(self):
        return ledger.read_book(self.user, self.book['id'])['revision']

    def lines(self, parts):
        return [{'account_id': self.accounts[code], 'debit': debit, 'credit': credit} for code, debit, credit in parts]

    def entry(self, date, description, parts, post=True):
        made = ledger.propose_journal(self.user, self.book['id'], {
            'operation_id': str(uuid.uuid4()), 'expected_revision': self.revision(), 'date': date, 'description': description,
            'source': {'kind': 'manual', 'reference': f'synthetic-{uuid.uuid4()}'}, 'evidence': ['synthetic'], 'lines': self.lines(parts)})
        if post:
            ledger.confirm_own_journal(self.user, self.book['id'], made['id'], {'expected_revision': self.revision(), 'payload_hash': made['payload_hash'], 'confirm': True})
        return made

    def restrict(self, restricted=True, account='1050', user=None):
        return approvals.request_action(user or self.user, {'operation': 'account.restricted', 'summary': 'Keep the tax savings out of usable cash',
            'payload': {'book_id': self.book['id'], 'account_id': self.accounts[account], 'restricted': restricted}})

    def approve(self, item):
        return approvals.decide(self.user, item['id'], {'decision': 'approve', 'payload_hash': item['payload_hash']})

    def forecast(self, start='2026-03-02', user=None):
        return ledger.cash_forecast(user or self.user, self.book['id'], {'from': start})

    def known(self):
        ledger.create_schedule(self.user, self.book['id'], {'expected_revision': self.revision(), 'description': 'Office rent',
            'lines': self.lines((('6200', '900', '0'), ('1000', '0', '900'))), 'day': 5, 'starts': '2026-03-01'})
        self.entry('2026-03-12', 'Supplier payment', (('6200', '250', '0'), ('1000', '0', '250')), post=False)
        self.entry('2026-03-20', 'Client receipt', (('1000', '400', '0'), ('4000', '0', '400')))
        self.entry('2026-03-25', 'Supplier bill, not paid yet', (('6200', '300', '0'), ('2000', '0', '300')))

    def test_restricted_cash_is_set_aside_only_by_approval_and_left_out_of_usable_cash(self):
        before = self.forecast()
        self.assertEqual((before['opening_usable'], before['restricted_cash']), ('5000.0000', '0.0000'))
        item = self.restrict()
        self.assertEqual(item['detail'], {'books': 'Synthetic forecast business', 'account': '1050 Tax savings', 'restricted': False, 'balance': '1000.0000', 'currency': 'USD'})
        self.assertEqual(self.forecast()['restricted_cash'], '0.0000')                          # Nothing changes until approved.
        journals, revision = Journal.objects.count(), self.revision()
        done = self.approve(item)
        self.assertEqual((done['state'], done['result']), ('approved', {'account_id': self.accounts['1050'], 'restricted': True, 'revision': revision + 1}))
        after = self.forecast()
        self.assertEqual((after['opening_usable'], after['restricted_cash']), ('4000.0000', '1000.0000'))
        self.assertEqual([(a['code'], a['restricted'], a['balance']) for a in after['cash_accounts']], [('1000', False, '4000.0000'), ('1050', True, '1000.0000')])
        self.assertEqual((Journal.objects.count(), LedgerEvent.objects.filter(action='account.restricted').count()), (journals, 1))
        self.assertEqual(self.approve(item)['result'], done['result'])
        # It can be released again, and a restricted account cannot stop being a bank or cash account.
        with self.assertRaises(ledger.LedgerError) as refused:
            ledger.cash_change(ledger.owned(self.user, self.book['id'])[1], self.accounts['1050'], False)
        self.assertEqual(refused.exception.code, 'ledger_account_restricted')
        self.assertEqual(self.approve(self.restrict(False))['state'], 'approved')
        self.assertEqual(self.forecast()['opening_usable'], '5000.0000')

    def test_thirteen_weeks_carry_every_known_movement_of_usable_cash_once(self):
        self.approve(self.restrict())
        self.known()
        value = self.forecast()
        self.assertEqual((value['from'], value['through'], value['currency'], value['opening_usable'], value['closing_usable'], len(value['weeks'])),
                         ('2026-03-02', '2026-05-31', 'USD', '4000.0000', '1450.0000', 13))
        weeks = {week['week']: week for week in value['weeks']}
        self.assertEqual(weeks[1], {'week': 1, 'start': '2026-03-02', 'end': '2026-03-08', 'in': '0.0000', 'out': '900.0000', 'net': '-900.0000', 'closing': '3100.0000',
                                    'items': [{'date': '2026-03-05', 'description': 'Office rent', 'amount': '-900.0000', 'source': 'recurring'}]})
        self.assertEqual(weeks[2]['items'], [{'date': '2026-03-12', 'description': 'Supplier payment', 'amount': '-250.0000', 'source': 'draft'}])
        self.assertEqual(weeks[3]['items'], [{'date': '2026-03-20', 'description': 'Client receipt', 'amount': '400.0000', 'source': 'posted'}])
        self.assertEqual([(n, weeks[n]['closing']) for n in (2, 3, 4, 5, 9, 10, 13)],
                         [(2, '2850.0000'), (3, '3250.0000'), (4, '3250.0000'), (5, '2350.0000'), (9, '2350.0000'), (10, '1450.0000'), (13, '1450.0000')])
        self.assertEqual((weeks[13]['start'], weeks[13]['end'], weeks[4]['items']), ('2026-05-25', '2026-05-31', []))     # The unpaid bill moves no cash.
        self.assertEqual(value['lowest'], {'week': 10, 'closing': '1450.0000'})
        self.assertEqual(value['sources'], {'posted': 1, 'draft': 1, 'recurring': 3})
        self.assertEqual(sum(len(week['items']) for week in value['weeks']), 5)
        # A recurring occurrence that already has a draft counts once, as that draft.
        ledger.prepare_due(self.user, self.book['id'], {'through': '2026-03-31'})
        again = self.forecast()
        self.assertEqual((again['weeks'][0]['items'][0]['source'], again['sources'], again['closing_usable']), ('draft', {'posted': 1, 'draft': 2, 'recurring': 2}, '1450.0000'))

    def test_what_is_already_late_lands_in_the_first_week_and_restricted_or_non_cash_movements_are_left_out(self):
        self.approve(self.restrict())
        self.known()
        self.entry('2026-04-10', 'More set aside for tax', (('1050', '600', '0'), ('1000', '0', '600')))
        value = self.forecast('2026-04-06')
        # Opening is cash before the start; March's unposted draft and the undrafted rents are late and still expected.
        self.assertEqual((value['opening_usable'], value['restricted_cash']), ('4400.0000', '1000.0000'))
        self.assertEqual(value['weeks'][0]['items'], [{'date': '2026-03-05', 'description': 'Office rent', 'amount': '-900.0000', 'source': 'recurring'},
            {'date': '2026-03-12', 'description': 'Supplier payment', 'amount': '-250.0000', 'source': 'draft'},
            {'date': '2026-04-05', 'description': 'Office rent', 'amount': '-900.0000', 'source': 'recurring'},
            {'date': '2026-04-10', 'description': 'More set aside for tax', 'amount': '-600.0000', 'source': 'posted'}])
        self.assertEqual((value['weeks'][0]['closing'], value['lowest']), ('1750.0000', {'week': 13, 'closing': '-950.0000'}))
        self.assertEqual(Journal.objects.filter(state='posted').count(), 5)                     # Reading the forecast posts nothing.

    def test_forecasts_and_restrictions_for_bad_dates_accounts_and_other_people_are_refused(self):
        for start in ('2025-12-29', '2027-01-04', 'March', None, 20260302):
            with self.assertRaises(ledger.LedgerError, msg=start):
                self.forecast(start)
        for bad in ({}, {'from': '2026-03-02', 'weeks': 26}):
            with self.assertRaises(ledger.LedgerError):
                ledger.cash_forecast(self.user, self.book['id'], bad)
        with self.assertRaises(ledger.LedgerError):
            self.forecast(user=self.other)
        cases = ((dict(account='6200'), 'ledger_restricted_needs_cash'), (dict(account='2000'), 'ledger_restricted_needs_cash'), (dict(restricted=False), 'ledger_restricted_unchanged'),
                 (dict(restricted='yes'), None), (dict(user=self.other), None))
        for changes, code in cases:
            with self.assertRaises(approvals.ApprovalError, msg=changes) as refused:
                self.restrict(**changes)
            if code:
                self.assertEqual(refused.exception.code, code, changes)
        with self.assertRaises(approvals.ApprovalError):
            approvals.request_action(self.user, {'operation': 'account.restricted', 'summary': 'Padded', 'payload': {
                'book_id': self.book['id'], 'account_id': self.accounts['1050'], 'restricted': True, 'force': True}})
        self.assertEqual((ActionRequest.objects.count(), Account.objects.filter(restricted=True).count()), (0, 0))

    def test_the_agent_tool_reads_the_forecast_and_changes_nothing(self):
        self.known()
        broker, _, _ = BookkeepingPostingTests.broker(self)
        tool = lambda arguments: broker.call('maipa_books_forecast', {'book_id': self.book['id'], **arguments})
        revision = self.revision()
        self.assertEqual(json.loads(tool({'from': '2026-03-02'})['content'][0]['text']), self.forecast())
        for bad in ({}, {'from': 'soon'}, {'from': '2026-03-02', 'weeks': 26}, {'from': '2026-03-02', 'restricted': True}):
            self.assertTrue(tool(bad)['isError'], bad)
        self.assertEqual(self.revision(), revision)
