"""Accruals: one approval posts an adjusting entry and its exact opposite on a later date, so it counts only in its own period."""
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.maipa.approvals import services as approvals
from backend.maipa.approvals.models import ActionRequest
from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.ledger import services as ledger
from backend.maipa.ledger.models import Journal, LedgerEvent
from backend.maipa.workspace.models import Installation, Membership

PERIOD = {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'}


class AccrualTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='accrual-owner')
        cls.other = get_user_model().objects.create_user(username='accrual-other')
        Membership.objects.create(user=cls.user, installation=install, role='preparer')
        Membership.objects.create(user=cls.other, installation=install, role='administrator')
        cls.entity = Entity.objects.create(engagement=Engagement.objects.create(
            owner=cls.user, installation=install, name='Synthetic accruals'), name='Synthetic accrual business')

    def setUp(self):
        self.book = ledger.create_book(self.user, self.entity.pk, PERIOD)
        self.accounts = {a['code']: a['id'] for a in self.book['accounts']}
        self.lines = [{'account_id': self.accounts['6200'], 'debit': '1200', 'credit': '0'},
                      {'account_id': self.accounts['2000'], 'debit': '0', 'credit': '1200'}]

    def revision(self):
        return ledger.read_book(self.user, self.book['id'])['revision']

    def ask(self, user=None, **changes):
        payload = {'book_id': self.book['id'], 'date': '2026-03-31', 'description': 'March utilities not yet billed', 'lines': self.lines, 'reverse_on': '2026-04-01', **changes}
        return approvals.request_action(user or self.user, {'operation': 'accrual.post', 'summary': 'Accrue March utilities', 'payload': payload})

    def approve(self, item):
        return approvals.decide(self.user, item['id'], {'decision': 'approve', 'payload_hash': item['payload_hash']})

    def report(self, start, end):
        return ledger.financial_report(self.user, self.book['id'], {'from': start, 'to': end})

    def test_one_approval_posts_the_accrual_and_its_reversal_so_it_counts_only_in_its_month(self):
        item = self.ask()
        self.assertEqual(item['detail'], {'books': 'Synthetic accrual business', 'currency': 'USD', 'description': 'March utilities not yet billed',
            'date': '2026-03-31', 'reverse_on': '2026-04-01', 'total': '1200.0000',
            'lines': [{'account': '6200 Office expenses', 'debit': '1200.0000', 'credit': '0.0000'}, {'account': '2000 Accounts payable', 'debit': '0.0000', 'credit': '1200.0000'}]})
        self.assertEqual(Journal.objects.count(), 0)
        before = self.revision()
        done = self.approve(item)
        self.assertEqual(done['state'], 'approved')
        accrual, reversal = Journal.objects.get(pk=done['result']['journal_id']), Journal.objects.get(pk=done['result']['reversal_id'])
        self.assertEqual((accrual.state, accrual.date.isoformat(), accrual.source_kind, accrual.description), ('posted', '2026-03-31', 'accrual', 'March utilities not yet billed'))
        self.assertEqual((reversal.state, reversal.date.isoformat(), reversal.source_kind, reversal.source_reference), ('posted', '2026-04-01', 'reversal', str(accrual.pk)))
        self.assertEqual(self.revision(), before + 4)                         # Propose and post the accrual, then the reversal.
        self.assertEqual([(r['code'], r['amount']) for r in self.report('2026-03-01', '2026-03-31')['income_statement']['expenses']], [('6200', '1200.0000')])
        self.assertEqual([(r['code'], r['amount']) for r in self.report('2026-04-01', '2026-04-30')['income_statement']['expenses']], [('6200', '-1200.0000')])
        self.assertEqual(self.report('2026-03-01', '2026-04-30')['income_statement']['expenses'], [])
        self.assertEqual(ledger.verify_book(self.user, self.book['id'])['findings'], [])
        self.assertEqual(self.approve(item)['result'], done['result'])
        self.assertEqual(Journal.objects.count(), 2)
        self.assertEqual(LedgerEvent.objects.filter(action='journal.post').count(), 2)

    def test_dates_lines_and_requesters_are_checked_before_anything_is_saved(self):
        ledger.change_period(self.user, self.book['id'], {'expected_revision': self.revision(), 'action': 'close', 'through': '2026-02-28'})
        cases = ((dict(reverse_on='2026-03-31'), 'accrual_reverse_not_after'), (dict(reverse_on='2026-03-15'), 'accrual_reverse_not_after'),
                 (dict(date='2026-02-28', reverse_on='2026-03-01'), 'ledger_period_closed'), (dict(reverse_on='2027-01-01'), 'ledger_date_outside_period'),
                 (dict(lines=[dict(self.lines[0], debit='1201'), self.lines[1]]), 'ledger_unbalanced'), (dict(lines=[self.lines[0]]), None),
                 (dict(date='March'), None), (dict(description=''), None), (dict(reverse_on=None), None), (dict(force=True), None))
        for changes, code in cases:
            with self.assertRaises(approvals.ApprovalError, msg=changes) as refused:
                self.ask(**changes)
            if code:
                self.assertEqual(refused.exception.code, code, changes)
        with self.assertRaises(approvals.ApprovalError):
            self.ask(self.other)
        self.assertEqual((ActionRequest.objects.count(), Journal.objects.count()), (0, 0))

    def test_a_close_after_the_request_makes_the_approval_fail_with_nothing_posted(self):
        item = self.ask()
        ledger.change_period(self.user, self.book['id'], {'expected_revision': self.revision(), 'action': 'close', 'through': '2026-03-31'})
        done = self.approve(item)
        self.assertEqual((done['state'], done['error'], Journal.objects.count()), ('failed', 'ledger_period_closed', 0))
        # The reversal date being closed is caught too, before the accrual posts.
        later = self.ask(date='2026-04-29', reverse_on='2026-04-30')
        ledger.change_period(self.user, self.book['id'], {'expected_revision': self.revision(), 'action': 'close', 'through': '2026-04-30'})
        self.assertEqual((self.approve(later)['state'], Journal.objects.count()), ('failed', 0))
        self.assertEqual(uuid.UUID(str(later['id'])).version, 4)
