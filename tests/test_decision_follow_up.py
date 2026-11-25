"""Decision follow-up: a decision the person asked to be reminded of, with a review date and, later, what came of it."""
from datetime import date
import json
from unittest.mock import patch
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.maipa.approvals import services as approvals
from backend.maipa.approvals.models import ActionRequest
from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.ledger import decisions, services as ledger
from backend.maipa.ledger.models import Decision, Journal
from backend.maipa.workspace.models import Installation, Membership
from tests.test_bookkeeping_posting import BookkeepingPostingTests

PERIOD = {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'}


class DecisionFollowUpTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='decision-owner')
        cls.other = get_user_model().objects.create_user(username='decision-other')
        Membership.objects.create(user=cls.user, installation=install, role='preparer')
        Membership.objects.create(user=cls.other, installation=install, role='administrator')
        cls.entity = Entity.objects.create(engagement=Engagement.objects.create(
            owner=cls.user, installation=install, name='Synthetic decisions'), name='Synthetic decision business')

    def setUp(self):
        self.book = ledger.create_book(self.user, self.entity.pk, PERIOD)
        clock = patch('backend.maipa.ledger.decisions.today', return_value=date(2026, 3, 10)); clock.start(); self.addCleanup(clock.stop)

    def ask(self, user=None, **changes):
        payload = {'book_id': self.book['id'], 'title': 'Hold off hiring an assistant', 'note': 'Cash goes below zero with the hire. Look again once the spring invoices are paid.',
                   'review_on': '2026-04-15', **changes}
        return approvals.request_action(user or self.user, {'operation': 'decision.record', 'summary': 'Record the decision to wait on hiring', 'payload': payload})

    def approve(self, item):
        return approvals.decide(self.user, item['id'], {'decision': 'approve', 'payload_hash': item['payload_hash']})

    def recorded(self, **changes):
        done = self.approve(self.ask(**changes))
        self.assertEqual(done['state'], 'approved', done)
        return done['result']['decision_id']

    def listed(self, as_of, user=None):
        return decisions.listing(user or self.user, self.book['id'], {'as_of': as_of})

    def close(self, decision, outcome='Hired in May after the invoices were paid.', user=None):
        return approvals.request_action(user or self.user, {'operation': 'decision.close', 'summary': 'Close the hiring decision',
                                                            'payload': {'book_id': self.book['id'], 'decision_id': decision, 'outcome': outcome}})

    def test_a_recorded_decision_comes_due_on_its_review_date_and_is_closed_with_its_outcome(self):
        item = self.ask()
        self.assertEqual(item['detail'], {'books': 'Synthetic decision business', 'title': 'Hold off hiring an assistant',
            'note': 'Cash goes below zero with the hire. Look again once the spring invoices are paid.', 'review_on': '2026-04-15', 'decided_on': '2026-03-10', 'current': True})
        self.assertEqual(Decision.objects.count(), 0)
        journals, revision = Journal.objects.count(), ledger.read_book(self.user, self.book['id'])['revision']
        done = self.approve(item)
        self.assertEqual(done['state'], 'approved')
        decision = done['result']['decision_id']
        shown = {'id': decision, 'title': 'Hold off hiring an assistant', 'note': 'Cash goes below zero with the hire. Look again once the spring invoices are paid.',
                 'decided_on': '2026-03-10', 'review_on': '2026-04-15', 'outcome': None, 'closed_on': None}
        self.assertEqual(self.listed('2026-04-14'), {'book_id': self.book['id'], 'as_of': '2026-04-14', 'due': 0, 'decisions': [{**shown, 'state': 'open'}]})
        self.assertEqual(self.listed('2026-04-15'), {'book_id': self.book['id'], 'as_of': '2026-04-15', 'due': 1, 'decisions': [{**shown, 'state': 'due'}]})
        closing = self.close(decision)
        self.assertEqual(closing['detail'], {'books': 'Synthetic decision business', 'title': 'Hold off hiring an assistant', 'review_on': '2026-04-15',
                                             'outcome': 'Hired in May after the invoices were paid.', 'current': True})
        self.assertEqual(self.listed('2026-05-20')['due'], 1)                              # Still due until the outcome is approved.
        with patch('backend.maipa.ledger.decisions.today', return_value=date(2026, 5, 20)):
            closed = self.approve(closing)
        self.assertEqual((closed['state'], closed['result']), ('approved', {'decision_id': decision, 'closed': True}))
        self.assertEqual(self.listed('2026-05-21'), {'book_id': self.book['id'], 'as_of': '2026-05-21', 'due': 0,
            'decisions': [{**shown, 'state': 'closed', 'outcome': 'Hired in May after the invoices were paid.', 'closed_on': '2026-05-20'}]})
        # A decision is a note beside the books: no entry and no book revision; approving again changes nothing.
        self.assertEqual((Journal.objects.count(), ledger.read_book(self.user, self.book['id'])['revision'], self.approve(item)['result'], Decision.objects.count()),
                         (journals, revision, done['result'], 1))
        with self.assertRaises(approvals.ApprovalError) as refused:
            self.close(decision, 'Closed twice')
        self.assertEqual(refused.exception.code, 'decision_already_closed')

    def test_due_decisions_come_first_then_open_ones_by_date_then_closed_ones(self):
        later = self.recorded(title='Review the software subscriptions', review_on='2026-06-01')
        soon = self.recorded(title='Revisit the price increase', review_on='2026-03-20')
        overdue = self.recorded(title='Decide on the second van', review_on='2026-03-12')
        done = self.recorded(title='Switch bank accounts', review_on='2026-03-11')
        self.approve(self.close(done, 'Stayed with the current bank.'))
        value = self.listed('2026-03-25')
        self.assertEqual((value['due'], [(item['id'], item['state']) for item in value['decisions']]),
                         (2, [(overdue, 'due'), (soon, 'due'), (later, 'open'), (done, 'closed')]))

    def test_malformed_decisions_past_review_dates_and_other_people_are_refused_before_anything_is_saved(self):
        cases = ((dict(review_on='2026-03-09'), 'decision_review_in_past'), (dict(review_on='2036-03-11'), 'decision_review_too_far'), (dict(review_on='soon'), None),
                 (dict(title=''), None), (dict(title='x' * 201), None), (dict(title=' padded '), None), (dict(note=''), None), (dict(note='x' * 1001), None),
                 (dict(note=5), None), (dict(force=True), None))
        for changes, code in cases:
            with self.assertRaises(approvals.ApprovalError, msg=changes) as refused:
                self.ask(**changes)
            if code:
                self.assertEqual(refused.exception.code, code, changes)
        with self.assertRaises(approvals.ApprovalError):
            self.ask(self.other)
        self.assertEqual((ActionRequest.objects.count(), Decision.objects.count()), (0, 0))
        decision = self.recorded()
        for user, target, outcome in ((self.other, decision, 'Not mine'), (self.user, str(uuid.uuid4()), 'Unknown'), (self.user, 'x', 'Malformed'), (self.user, decision, ''),
                                      (self.user, decision, 'x' * 1001)):
            with self.assertRaises(approvals.ApprovalError, msg=(target, outcome)):
                self.close(target, outcome, user)
        for bad in ({}, {'as_of': 'today'}, {'as_of': '2026-03-25', 'state': 'due'}):
            with self.assertRaises(ledger.LedgerError, msg=bad):
                decisions.listing(self.user, self.book['id'], bad)
        with self.assertRaises(ledger.LedgerError):
            self.listed('2026-03-25', self.other)
        # A review date that has passed by the time of approval fails rather than recording a stale reminder.
        waiting = self.ask(title='Approved too late', review_on='2026-03-11')
        with patch('backend.maipa.ledger.decisions.today', return_value=date(2026, 3, 12)):
            late = self.approve(waiting)
        self.assertEqual((late['state'], late['error'], Decision.objects.count()), ('failed', 'decision_review_in_past', 1))

    def test_the_agent_tool_lists_decisions_and_cannot_record_or_close_one(self):
        decision = self.recorded()
        broker, _, _ = BookkeepingPostingTests.broker(self)
        tool = lambda arguments: broker.call('maipa_books_decisions', {'book_id': self.book['id'], **arguments})
        self.assertEqual(json.loads(tool({'as_of': '2026-04-15'})['content'][0]['text']), self.listed('2026-04-15'))
        for bad in ({}, {'as_of': 'today'}, {'as_of': '2026-04-15', 'decision_id': decision, 'outcome': 'Done'}, {'as_of': '2026-04-15', 'title': 'New decision'}):
            self.assertTrue(tool(bad)['isError'], bad)
        self.assertEqual((Decision.objects.count(), Decision.objects.filter(closed_on__isnull=False).count()), (1, 0))
