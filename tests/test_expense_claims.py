"""Expense claims: business costs someone paid personally, recorded as owed to them and reimbursed, each by approval."""
import json
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.maipa.approvals import services as approvals
from backend.maipa.approvals.models import ActionRequest
from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.ledger import claims, services as ledger
from backend.maipa.ledger.models import ExpenseClaim, Journal
from backend.maipa.workspace.models import Installation, Membership
from tests.test_bookkeeping_posting import BookkeepingPostingTests

PERIOD = {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'}


class ExpenseClaimTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='claim-owner')
        cls.other = get_user_model().objects.create_user(username='claim-other')
        Membership.objects.create(user=cls.user, installation=install, role='preparer')
        Membership.objects.create(user=cls.other, installation=install, role='administrator')
        cls.entity = Entity.objects.create(engagement=Engagement.objects.create(
            owner=cls.user, installation=install, name='Synthetic claims'), name='Synthetic claim business')

    def setUp(self):
        self.book = ledger.create_book(self.user, self.entity.pk, PERIOD)
        ledger.create_account(self.user, self.book['id'], {'expected_revision': self.revision(), 'code': '6400', 'name': 'Travel', 'category': 'expense'})
        ledger.create_account(self.user, self.book['id'], {'expected_revision': self.revision(), 'code': '2150', 'name': 'Owed to staff', 'category': 'liability'})
        self.accounts = {a['code']: a['id'] for a in ledger.read_book(self.user, self.book['id'])['accounts']}
        made = ledger.propose_journal(self.user, self.book['id'], {
            'operation_id': str(uuid.uuid4()), 'expected_revision': self.revision(), 'date': '2026-02-10', 'description': 'Client payment',
            'source': {'kind': 'manual', 'reference': 'synthetic-sale'}, 'evidence': ['synthetic'],
            'lines': [{'account_id': self.accounts['1000'], 'debit': '5000', 'credit': '0'}, {'account_id': self.accounts['4000'], 'debit': '0', 'credit': '5000'}]})
        ledger.confirm_own_journal(self.user, self.book['id'], made['id'], {'expected_revision': self.revision(), 'payload_hash': made['payload_hash'], 'confirm': True})

    def revision(self):
        return ledger.read_book(self.user, self.book['id'])['revision']

    def request(self, operation, payload, user=None):
        return approvals.request_action(user or self.user, {'operation': operation, 'summary': 'Synthetic claim change', 'payload': {'book_id': self.book['id'], **payload}})

    def approve(self, item):
        return approvals.decide(self.user, item['id'], {'decision': 'approve', 'payload_hash': item['payload_hash']})

    def lines(self):
        return [{'date': '2026-03-04', 'description': 'Train to client site', 'amount': '64.20', 'account_id': self.accounts['6400'], 'receipt': 'RAIL-88213'},
                {'date': '2026-03-04', 'description': 'Printer ink', 'amount': '35.80', 'account_id': self.accounts['6200'], 'receipt': 'STORE-4410'}]

    def claim(self, user=None, **changes):
        payload = {'claimant': 'Sam Rivera', 'date': '2026-03-06', 'lines': self.lines(), 'payable_account_id': self.accounts['2150'], **changes}
        return self.request('claim.record', payload, user)

    def recorded(self, **changes):
        done = self.approve(self.claim(**changes))
        self.assertEqual(done['state'], 'approved', done)
        return done['result']['claim_id']

    def reimburse(self, claim, date='2026-03-20', cash='1000', user=None):
        return self.request('claim.reimburse', {'claim_id': claim, 'date': date, 'cash_account_id': self.accounts[cash]}, user)

    def outstanding(self, user=None):
        return claims.outstanding(user or self.user, self.book['id'])

    def test_a_claim_is_recorded_as_owed_to_the_person_and_reimbursed_by_approval(self):
        item = self.claim()
        self.assertEqual(item['detail'], {'books': 'Synthetic claim business', 'currency': 'USD', 'claimant': 'Sam Rivera', 'date': '2026-03-06', 'total': '100.0000',
            'payable': '2150 Owed to staff', 'possible_duplicates': [], 'current': True,
            'lines': [{'date': '2026-03-04', 'description': 'Train to client site', 'amount': '64.2000', 'account': '6400 Travel', 'receipt': 'RAIL-88213'},
                      {'date': '2026-03-04', 'description': 'Printer ink', 'amount': '35.8000', 'account': '6200 Office expenses', 'receipt': 'STORE-4410'}]})
        self.assertEqual((ExpenseClaim.objects.count(), Journal.objects.count()), (0, 1))
        done = self.approve(item)
        self.assertEqual(done['state'], 'approved')
        claim = ExpenseClaim.objects.get(pk=done['result']['claim_id'])
        entry = claim.journal
        self.assertEqual((claim.state, entry.state, entry.source_kind, entry.date.isoformat(), entry.description), ('open', 'posted', 'claim', '2026-03-06', 'Expense claim from Sam Rivera'))
        self.assertEqual([(l.account.code, l.debit_units, l.credit_units) for l in entry.lines.order_by('position')],
                         [('6400', 642000, 0), ('6200', 358000, 0), ('2150', 0, 1000000)])
        self.assertEqual(self.outstanding(), {'book_id': self.book['id'], 'currency': 'USD', 'total': '100.0000', 'recent': [],
            'people': [{'claimant': 'Sam Rivera', 'total': '100.0000', 'claims': [{'id': str(claim.pk), 'date': '2026-03-06', 'total': '100.0000', 'lines': 2}]}]})
        paying = self.reimburse(str(claim.pk))
        self.assertEqual(paying['detail'], {'books': 'Synthetic claim business', 'currency': 'USD', 'claimant': 'Sam Rivera', 'claim_date': '2026-03-06', 'total': '100.0000',
            'date': '2026-03-20', 'cash_account': '1000 Checking', 'payable': '2150 Owed to staff', 'current': True})
        paid = self.approve(paying)
        claim.refresh_from_db()
        self.assertEqual((paid['state'], claim.state, claim.paid_on.isoformat(), claim.payment_journal.source_kind), ('approved', 'paid', '2026-03-20', 'claimpay'))
        self.assertEqual([(l.account.code, l.debit_units, l.credit_units) for l in claim.payment_journal.lines.order_by('position')], [('2150', 1000000, 0), ('1000', 0, 1000000)])
        after = self.outstanding()
        self.assertEqual((after['people'], after['total'], [(r['claimant'], r['state'], r['paid_on']) for r in after['recent']]), ([], '0.0000', [('Sam Rivera', 'paid', '2026-03-20')]))
        report = ledger.financial_report(self.user, self.book['id'], {'from': '2026-01-01', 'to': '2026-03-31'})
        self.assertEqual((report['income_statement']['total_expenses'], report['cash_flow']['closing_cash'], report['balance_sheet']['total_liabilities']), ('100.0000', '4900.0000', '0.0000'))
        self.assertEqual(ledger.verify_book(self.user, self.book['id'])['findings'], [])
        self.assertEqual((self.approve(item)['result'], self.approve(paying)['result'], Journal.objects.count()), (done['result'], paid['result'], 3))
        with self.assertRaises(approvals.ApprovalError) as refused:
            self.reimburse(str(claim.pk))
        self.assertEqual(refused.exception.code, 'claim_not_open')

    def test_a_receipt_is_claimed_once_and_a_look_alike_line_is_pointed_out(self):
        self.recorded()
        again = [dict(self.lines()[0], receipt='rail 88213')]
        with self.assertRaises(approvals.ApprovalError) as refused:
            self.claim(lines=again)
        self.assertEqual(refused.exception.code, 'claim_receipt_already_claimed')
        twice = [self.lines()[1], dict(self.lines()[1], description='Printer ink again')]
        with self.assertRaises(approvals.ApprovalError) as refused:
            self.claim(lines=twice)
        self.assertEqual(refused.exception.code, 'claim_receipt_repeated')
        # The same person, the same day and amount, without a receipt: allowed, and shown.
        alike = self.claim(lines=[{'date': '2026-03-04', 'description': 'Train ticket', 'amount': '64.20', 'account_id': self.accounts['6400']}])
        self.assertEqual(alike['detail']['possible_duplicates'], [{'claimant': 'Sam Rivera', 'date': '2026-03-04', 'description': 'Train to client site', 'amount': '64.2000', 'state': 'open'}])
        self.assertIsNone(alike['detail']['lines'][0]['receipt'])
        # Someone else's identical line is not that person's duplicate.
        self.assertEqual(self.claim(claimant='Alex Chen', lines=[{'date': '2026-03-04', 'description': 'Train', 'amount': '64.20', 'account_id': self.accounts['6400']}])['detail']['possible_duplicates'], [])

    def test_claims_are_grouped_by_person_and_a_wrong_claim_is_voided_by_reversing_it(self):
        first = self.recorded()
        second = self.recorded(date='2026-03-12', lines=[{'date': '2026-03-11', 'description': 'Client lunch', 'amount': '48', 'account_id': self.accounts['6200']}])
        alex = self.recorded(claimant='Alex Chen', date='2026-03-10', lines=[{'date': '2026-03-09', 'description': 'Taxi', 'amount': '22.50', 'account_id': self.accounts['6400']}])
        listed = self.outstanding()
        self.assertEqual([(p['claimant'], p['total'], [c['id'] for c in p['claims']]) for p in listed['people']],
                         [('Alex Chen', '22.5000', [alex]), ('Sam Rivera', '148.0000', [first, second])])
        self.assertEqual(listed['total'], '170.5000')
        stop = self.request('claim.void', {'claim_id': second, 'date': '2026-03-13'})
        self.assertEqual(stop['detail'], {'books': 'Synthetic claim business', 'currency': 'USD', 'claimant': 'Sam Rivera', 'claim_date': '2026-03-12', 'total': '48.0000',
                                          'date': '2026-03-13', 'current': True})
        self.assertEqual(self.approve(stop)['state'], 'approved')
        voided = ExpenseClaim.objects.get(pk=second)
        self.assertEqual((voided.state, voided.void_journal.source_kind, voided.void_journal.source_reference), ('void', 'reversal', str(voided.journal_id)))
        self.assertEqual((self.outstanding()['total'], [(r['claimant'], r['state']) for r in self.outstanding()['recent']]), ('122.5000', [('Sam Rivera', 'void')]))
        for call in (lambda: self.reimburse(second), lambda: self.request('claim.void', {'claim_id': second, 'date': '2026-03-14'})):
            with self.assertRaises(approvals.ApprovalError) as refused:
                call()
            self.assertEqual(refused.exception.code, 'claim_not_open')

    def test_malformed_claims_wrong_accounts_and_other_people_are_refused_and_nothing_is_posted(self):
        line = self.lines()[0]
        cases = ((dict(claimant=''), None), (dict(claimant=' padded '), None), (dict(claimant='x' * 101), None), (dict(lines=[]), None), (dict(lines='all'), None),
                 (dict(lines=[dict(line, amount='0')]), None), (dict(lines=[dict(line, amount=64.2)]), None), (dict(lines=[dict(line, date='2026-03-07')]), 'claim_line_after_claim'),
                 (dict(lines=[dict(line, date='2025-12-30')]), 'ledger_date_outside_period'), (dict(lines=[dict(line, account_id=self.accounts['4000'])]), 'claim_account_invalid'),
                 (dict(lines=[dict(line, account_id=self.accounts['1000'])]), 'claim_account_invalid'), (dict(lines=[dict(line, extra=1)]), None),
                 (dict(payable_account_id=self.accounts['1000']), 'claim_payable_invalid'), (dict(date='2027-01-02'), 'ledger_date_outside_period'), (dict(force=True), None))
        for changes, code in cases:
            with self.assertRaises(approvals.ApprovalError, msg=changes) as refused:
                self.claim(**changes)
            if code:
                self.assertEqual(refused.exception.code, code, changes)
        with self.assertRaises(approvals.ApprovalError):
            self.claim(self.other)
        self.assertEqual((ActionRequest.objects.count(), ExpenseClaim.objects.count(), Journal.objects.count()), (0, 0, 1))
        claim = self.recorded()
        for changes, code in ((dict(date='2026-03-05'), 'claim_payment_before_claim'), (dict(cash='6200'), 'claim_cash_account_invalid'), (dict(user=self.other), None)):
            with self.assertRaises(approvals.ApprovalError, msg=changes) as refused:
                self.reimburse(claim, **changes)
            if code:
                self.assertEqual(refused.exception.code, code, changes)
        with self.assertRaises(ledger.LedgerError):
            self.outstanding(self.other)
        waiting = self.reimburse(claim, date='2026-03-25')
        ledger.change_period(self.user, self.book['id'], {'expected_revision': self.revision(), 'action': 'close', 'through': '2026-03-31'})
        done = self.approve(waiting)
        self.assertEqual((done['state'], done['error'], ExpenseClaim.objects.get(pk=claim).state), ('failed', 'ledger_period_closed', 'open'))

    def test_the_agent_tool_lists_outstanding_claims_and_cannot_change_them(self):
        claim = self.recorded()
        broker, _, _ = BookkeepingPostingTests.broker(self)
        tool = lambda arguments: broker.call('maipa_books_claims', {'book_id': self.book['id'], **arguments})
        self.assertEqual(json.loads(tool({})['content'][0]['text']), self.outstanding())
        for bad in ({'claim_id': claim}, {'claimant': 'Sam Rivera', 'lines': []}):
            self.assertTrue(tool(bad)['isError'], bad)
        self.assertEqual((ExpenseClaim.objects.count(), ExpenseClaim.objects.get(pk=claim).state), (1, 'open'))
