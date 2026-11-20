"""A draft entry can be discarded by approval: it leaves the books' working views, frees its statement line and stays on record."""
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.maipa.approvals import services as approvals
from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.ledger import services as ledger
from backend.maipa.ledger.models import Journal, JournalLine, LedgerEvent
from backend.maipa.workspace.models import Installation, Membership

PERIOD = {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'}
MARCH = 'date,description,amount\n2026-03-05,Synthetic rent,-900\n2026-03-20,Bank fee,-25\n'


class DiscardDraftTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='discard-owner')
        cls.other = get_user_model().objects.create_user(username='discard-other')
        Membership.objects.create(user=cls.user, installation=install, role='preparer')
        Membership.objects.create(user=cls.other, installation=install, role='administrator')
        cls.entity = Entity.objects.create(engagement=Engagement.objects.create(
            owner=cls.user, installation=install, name='Synthetic discards'), name='Synthetic discard business')

    def setUp(self):
        self.book = ledger.create_book(self.user, self.entity.pk, PERIOD)
        self.accounts = {a['code']: a['id'] for a in self.book['accounts']}
        ledger.import_statement(self.user, self.book['id'], {'expected_revision': self.revision(), 'filename': 'march.csv', 'content': MARCH,
                                                             'account_id': self.accounts['1000']})

    def revision(self):
        return ledger.read_book(self.user, self.book['id'])['revision']

    def row(self, description):
        return next(r for r in ledger.source_rows(self.user, self.book['id'])['rows'] if r['description'] == description)

    def draft(self, description='Synthetic rent', expense='6200', amount='900', day='2026-03-05', from_row=True):
        reference = self.row(description)['id'] if from_row else f'synthetic-{uuid.uuid4()}'
        return ledger.propose_journal(self.user, self.book['id'], {
            'operation_id': str(uuid.uuid4()), 'expected_revision': self.revision(), 'date': day, 'description': description,
            'source': {'kind': 'csv' if from_row else 'manual', 'reference': reference}, 'evidence': [reference],
            'lines': [{'account_id': self.accounts[expense], 'debit': amount, 'credit': '0'}, {'account_id': self.accounts['1000'], 'debit': '0', 'credit': amount}]})

    def ask(self, operation, user=None, **payload):
        return approvals.request_action(user or self.user, {'operation': operation, 'summary': 'Synthetic request', 'payload': {'book_id': self.book['id'], **payload}})

    def approve(self, item):
        return approvals.decide(self.user, item['id'], {'decision': 'approve', 'payload_hash': item['payload_hash']})

    def test_an_approved_discard_removes_the_draft_from_working_views_and_frees_its_statement_line(self):
        made = self.draft()
        self.assertEqual((self.row('Synthetic rent')['status'], ledger.journal_preview(self.user, self.book['id'])['pending_count']), ('proposed', 1))
        item = self.ask('journal.discard', journal_id=made['id'])
        self.assertEqual(item['detail'], {'date': '2026-03-05', 'description': 'Synthetic rent', 'currency': 'USD', 'current': True,
            'lines': [{'account': '6200 Office expenses', 'debit': '900.0000', 'credit': '0.0000'}, {'account': '1000 Checking', 'debit': '0.0000', 'credit': '900.0000'}]})
        self.assertEqual(Journal.objects.get(pk=made['id']).state, 'draft')
        before = self.revision()
        done = self.approve(item)
        self.assertEqual((done['state'], done['result']), ('approved', {'journal_id': made['id'], 'state': 'discarded'}))
        book = ledger.read_book(self.user, self.book['id'])
        self.assertEqual((book['revision'], book['journals']), (before + 1, []))
        preview = ledger.journal_preview(self.user, self.book['id'])
        self.assertEqual((preview['pending_count'], {(a['draft_debit'], a['draft_credit']) for a in preview['accounts']}), (0, {('0.0000', '0.0000')}))
        freed = self.row('Synthetic rent')
        self.assertEqual((freed['status'], freed['journal_id'], freed['via']), ('pending', None, None))
        activity = ledger.account_activity(self.user, self.book['id'], self.accounts['1000'])
        self.assertEqual((activity['pending'], activity['posted']), ([], []))
        # The discarded entry and its lines stay on record, with the event that discarded it.
        kept = Journal.objects.get(pk=made['id'])
        self.assertEqual((kept.state, JournalLine.objects.filter(journal=kept).count()), ('voided', 2))
        event = LedgerEvent.objects.get(book_id=self.book['id'], revision=before + 1)
        self.assertEqual((event.action, str(event.target)), ('journal.discard', made['id']))
        self.assertEqual(self.approve(item)['result'], done['result'])
        self.assertEqual(self.revision(), before + 1)
        self.assertEqual(ledger.verify_book(self.user, self.book['id'])['findings'], [])

    def test_a_freed_statement_line_can_get_a_corrected_entry_that_posts(self):
        wrong = self.draft(expense='6100')
        self.approve(self.ask('journal.discard', journal_id=wrong['id']))
        right = self.draft(expense='6200')
        self.assertNotEqual(right['id'], wrong['id'])
        self.assertEqual(self.row('Synthetic rent')['journal_id'], right['id'])
        posted = self.approve(self.ask('journal.post', journal_id=right['id'], payload_hash=right['payload_hash']))
        self.assertEqual((posted['state'], self.row('Synthetic rent')['status']), ('approved', 'posted'))
        report = ledger.financial_report(self.user, self.book['id'], {'from': '2026-03-01', 'to': '2026-03-31'})
        self.assertEqual([(r['code'], r['amount']) for r in report['income_statement']['expenses']], [('6200', '900.0000')])
        self.assertEqual([j['id'] for j in ledger.read_book(self.user, self.book['id'])['journals']], [right['id']])

    def test_a_discarded_draft_can_no_longer_be_posted_matched_or_block_closing_the_period(self):
        made = self.draft()
        waiting = self.ask('journal.post', journal_id=made['id'], payload_hash=made['payload_hash'])
        with self.assertRaises(approvals.ApprovalError) as blocked:
            self.ask('period.close', through='2026-03-31')
        self.assertEqual(blocked.exception.code, 'ledger_period_has_drafts')
        self.approve(self.ask('journal.discard', journal_id=made['id']))
        stale = self.approve(waiting)
        self.assertEqual((stale['state'], Journal.objects.get(pk=made['id']).state), ('failed', 'voided'))
        with self.assertRaises(ledger.LedgerError):
            ledger.confirm_own_journal(self.user, self.book['id'], made['id'], {'expected_revision': self.revision(), 'payload_hash': made['payload_hash'], 'confirm': True})
        with self.assertRaises(approvals.ApprovalError):
            self.ask('statement.match', row_id=self.row('Bank fee')['id'], journal_id=made['id'])
        closed = self.approve(self.ask('period.close', through='2026-03-31'))
        self.assertEqual(closed['state'], 'approved')

    def test_posted_missing_foreign_and_repeated_discards_are_refused(self):
        made = self.draft()
        posted = self.draft('Bank fee', '6100', '25', '2026-03-20')
        self.approve(self.ask('journal.post', journal_id=posted['id'], payload_hash=posted['payload_hash']))
        for payload, code in (({'journal_id': posted['id']}, 'ledger_not_draft'), ({'journal_id': str(uuid.uuid4())}, 'ledger_not_found'),
                              ({'journal_id': 'rent'}, None), ({'journal_id': made['id'], 'force': True}, None), ({}, None)):
            with self.assertRaises(approvals.ApprovalError, msg=payload) as refused:
                self.ask('journal.discard', **payload)
            if code:
                self.assertEqual(refused.exception.code, code, payload)
        with self.assertRaises(approvals.ApprovalError):
            self.ask('journal.discard', self.other, journal_id=made['id'])
        first, second = self.ask('journal.discard', journal_id=made['id']), self.ask('journal.discard', journal_id=made['id'])
        self.approve(first)
        again = self.approve(second)
        self.assertEqual((again['state'], again['error']), ('failed', 'ledger_not_draft'))
        with self.assertRaises(approvals.ApprovalError) as refused:
            self.ask('journal.discard', journal_id=made['id'])
        self.assertEqual(refused.exception.code, 'ledger_not_draft')
        with self.assertRaises(ledger.LedgerError):
            ledger.discard_journal(self.other, self.book['id'], posted['id'], {'expected_revision': self.revision()})
        self.assertEqual(sorted(Journal.objects.values_list('state', flat=True)), ['posted', 'voided'])
