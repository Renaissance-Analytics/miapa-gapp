"""Recurring entries: a template the person approves once; each due occurrence becomes a draft that still needs approval to post."""
import json
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.maipa.approvals import services as approvals
from backend.maipa.approvals.models import ActionRequest
from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.ledger import services as ledger
from backend.maipa.ledger.models import Journal, LedgerEvent, RecurringEntry
from backend.maipa.workspace.models import Installation, Membership

PERIOD = {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'}


class RecurringEntryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='recurring-owner')
        cls.other = get_user_model().objects.create_user(username='recurring-other')
        Membership.objects.create(user=cls.user, installation=install, role='preparer')
        Membership.objects.create(user=cls.other, installation=install, role='administrator')
        cls.entity = Entity.objects.create(engagement=Engagement.objects.create(
            owner=cls.user, installation=install, name='Synthetic schedules'), name='Synthetic schedule business')

    def setUp(self):
        self.book = ledger.create_book(self.user, self.entity.pk, PERIOD)
        self.accounts = {a['code']: a['id'] for a in self.book['accounts']}
        self.rent = [{'account_id': self.accounts['6200'], 'debit': '900', 'credit': '0'},
                     {'account_id': self.accounts['1000'], 'debit': '0', 'credit': '900'}]

    def revision(self):
        return ledger.read_book(self.user, self.book['id'])['revision']

    def ask(self, operation, user=None, **payload):
        return approvals.request_action(user or self.user, {'operation': operation, 'summary': 'Synthetic request', 'payload': {'book_id': self.book['id'], **payload}})

    def approve(self, item):
        return approvals.decide(self.user, item['id'], {'decision': 'approve', 'payload_hash': item['payload_hash']})

    def schedule(self, **changes):
        payload = {'description': 'Office rent', 'lines': self.rent, 'day': 31, 'starts': '2026-01-15', **changes}
        return self.approve(self.ask('schedule.create', **payload))['result']

    def listed(self, through):
        return ledger.schedules(self.user, self.book['id'], {'through': through})

    def prepare(self, through):
        return ledger.prepare_due(self.user, self.book['id'], {'through': through})

    def test_an_approved_template_lists_its_due_dates_with_month_ends_clamped(self):
        item = self.ask('schedule.create', description='Office rent', lines=self.rent, day=31, starts='2026-01-15', ends='2026-06-30')
        self.assertEqual(item['detail'], {'books': 'Synthetic schedule business', 'currency': 'USD', 'description': 'Office rent', 'day': 31,
            'starts': '2026-01-15', 'ends': '2026-06-30', 'total': '900.0000',
            'lines': [{'account': '6200 Office expenses', 'debit': '900.0000', 'credit': '0.0000'}, {'account': '1000 Checking', 'debit': '0.0000', 'credit': '900.0000'}]})
        self.assertEqual(RecurringEntry.objects.count(), 0)
        before = self.revision()
        made = self.approve(item)['result']
        self.assertEqual((self.revision(), LedgerEvent.objects.filter(action='schedule.create').count()), (before + 1, 1))
        result = self.listed('2026-04-30')
        self.assertEqual(len(result['schedules']), 1)
        entry = result['schedules'][0]
        self.assertEqual({k: entry[k] for k in ('id', 'description', 'day', 'starts', 'ends', 'active', 'total')},
                         {'id': made['id'], 'description': 'Office rent', 'day': 31, 'starts': '2026-01-15', 'ends': '2026-06-30', 'active': True, 'total': '900.0000'})
        self.assertEqual(entry['due'], ['2026-01-31', '2026-02-28', '2026-03-31', '2026-04-30'])
        self.assertEqual(entry['next'], '2026-05-31')
        self.assertEqual(self.listed('2026-12-31')['schedules'][0]['due'][-1], '2026-06-30')
        self.assertEqual(self.listed('2026-01-20')['schedules'][0]['due'], [])
        self.assertEqual(ledger.schedules(self.user, self.book['id'], {'through': '2026-04-30'}), result)

    def test_preparing_due_entries_makes_each_draft_once_and_posting_still_needs_approval(self):
        made = self.schedule(day=5, starts='2026-01-01')
        before = Journal.objects.count()
        prepared = self.prepare('2026-03-10')
        self.assertEqual([(d['date'], d['description'], d['state'], d['source']) for d in prepared['drafts']],
                         [(f'2026-0{n}-05', 'Office rent', 'draft', {'kind': 'schedule', 'reference': f"{made['id']}:2026-0{n}-05"}) for n in (1, 2, 3)])
        self.assertEqual(Journal.objects.count(), before + 3)
        self.assertEqual(sorted(Journal.objects.values_list('state', flat=True).distinct()), ['draft'])
        self.assertEqual(self.prepare('2026-03-10')['drafts'], [])                      # The same occurrence is never drafted twice.
        self.assertEqual(self.listed('2026-03-10')['schedules'][0]['due'], [])
        first = prepared['drafts'][0]
        posted = self.approve(self.ask('journal.post', journal_id=first['id'], payload_hash=first['payload_hash']))
        self.assertEqual((posted['state'], Journal.objects.get(pk=first['id']).state), ('approved', 'posted'))
        # A discarded draft frees its occurrence for a fresh draft.
        second = prepared['drafts'][1]
        self.approve(self.ask('journal.discard', journal_id=second['id']))
        self.assertEqual(self.listed('2026-03-10')['schedules'][0]['due'], ['2026-02-05'])
        again = self.prepare('2026-03-10')['drafts']
        self.assertEqual([(d['date'], d['source']['reference']) for d in again], [('2026-02-05', f"{made['id']}:2026-02-05")])

    def test_closed_months_are_skipped_and_a_stopped_template_makes_nothing_new(self):
        made = self.schedule(day=10, starts='2026-01-01')
        ledger.change_period(self.user, self.book['id'], {'expected_revision': self.revision(), 'action': 'close', 'through': '2026-02-28'})
        self.assertEqual(self.listed('2026-04-30')['schedules'][0]['due'], ['2026-03-10', '2026-04-10'])
        stop = self.ask('schedule.stop', schedule_id=made['id'])
        self.assertEqual(stop['detail'], {'books': 'Synthetic schedule business', 'description': 'Office rent', 'day': 10, 'starts': '2026-01-01', 'ends': None, 'active': True})
        self.approve(stop)
        entry = self.listed('2026-04-30')['schedules'][0]
        self.assertEqual((entry['active'], entry['due'], entry['next']), (False, [], None))
        self.assertEqual(self.prepare('2026-04-30')['drafts'], [])
        with self.assertRaises(approvals.ApprovalError) as refused:
            self.ask('schedule.stop', schedule_id=made['id'])
        self.assertEqual(refused.exception.code, 'schedule_not_active')

    def test_unbalanced_foreign_or_malformed_templates_are_refused_before_anything_is_saved(self):
        other_book = ledger.create_book(self.user, Entity.objects.create(engagement=Engagement.objects.get(), name='Second business').pk, PERIOD)
        foreign = next(a['id'] for a in other_book['accounts'] if a['code'] == '6200')
        unbalanced = [dict(self.rent[0], debit='901'), self.rent[1]]
        cases = ((dict(lines=unbalanced), 'ledger_unbalanced'), (dict(lines=[self.rent[0]]), None), (dict(lines=[dict(self.rent[0], account_id=foreign), self.rent[1]]), 'ledger_account_not_found'),
                 (dict(lines=[dict(self.rent[0], credit='900'), self.rent[1]]), None), (dict(day=0), None), (dict(day=32), None), (dict(day='5'), None),
                 (dict(starts='2025-12-01'), 'ledger_date_outside_period'), (dict(starts='2026-02-30'), None), (dict(starts='2026-02-01', ends='2026-01-10'), 'schedule_ends_before_start'),
                 (dict(ends='2027-01-31'), 'ledger_date_outside_period'), (dict(description=''), None), (dict(description='x' * 501), None), (dict(amount='900'), None))
        for changes, code in cases:
            payload = {'description': 'Office rent', 'lines': self.rent, 'day': 5, 'starts': '2026-01-01', **changes}
            with self.assertRaises(approvals.ApprovalError, msg=changes) as refused:
                self.ask('schedule.create', **payload)
            if code:
                self.assertEqual(refused.exception.code, code, changes)
        with self.assertRaises(approvals.ApprovalError):
            self.ask('schedule.create', self.other, description='Office rent', lines=self.rent, day=5, starts='2026-01-01')
        with self.assertRaises(approvals.ApprovalError):
            self.ask('schedule.stop', schedule_id=str(uuid.uuid4()))
        self.assertEqual((RecurringEntry.objects.count(), ActionRequest.objects.count()), (0, 0))
        for value in ({}, {'through': 'May'}, {'through': '2027-01-01'}, {'through': '2026-05-31', 'post': True}):
            with self.assertRaises(ledger.LedgerError, msg=value):
                ledger.prepare_due(self.user, self.book['id'], value)
            with self.assertRaises(ledger.LedgerError, msg=value):
                ledger.schedules(self.user, self.book['id'], value)
        with self.assertRaises(ledger.LedgerError):
            ledger.prepare_due(self.other, self.book['id'], {'through': '2026-05-31'})

    def test_the_agent_tools_list_and_prepare_but_never_post(self):
        from tests.test_bookkeeping_posting import BookkeepingPostingTests
        self.schedule(day=5, starts='2026-01-01')
        broker, _, _ = BookkeepingPostingTests.broker(self)
        call = lambda name, arguments: broker.call(name, {'book_id': self.book['id'], **arguments})
        self.assertEqual(json.loads(call('maipa_books_schedules', {'through': '2026-02-28'})['content'][0]['text']), self.listed('2026-02-28'))
        prepared = json.loads(call('maipa_books_prepare_due', {'through': '2026-02-28'})['content'][0]['text'])
        self.assertEqual([d['date'] for d in prepared['drafts']], ['2026-01-05', '2026-02-05'])
        self.assertEqual(sorted(Journal.objects.values_list('state', flat=True)), ['draft', 'draft'])
        self.assertTrue(call('maipa_books_prepare_due', {})['isError'])
        self.assertTrue(call('maipa_books_prepare_due', {'through': '2026-02-28', 'post': True})['isError'])
