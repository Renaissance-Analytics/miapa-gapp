"""Overlapping statements repeat lines; a repeat is marked as a duplicate, by approval, instead of being booked again."""
import json
import uuid

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase

from backend.maipa.approvals import services as approvals
from backend.maipa.approvals.models import ActionRequest
from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.ledger import services as ledger
from backend.maipa.ledger.models import Journal, LedgerEvent, SourceRowMatch
from backend.maipa.workspace.models import Installation, Membership

PERIOD = {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'}
MARCH = 'date,description,amount\n2026-03-28,Synthetic rent,-900\n2026-03-30,Client payment,1250.5\n2026-03-30,Card fee,-2\n'
OVERLAP = 'date,description,amount\n2026-03-30,CLIENT PAYMENT,1250.5\n2026-03-30,Card fee,-2\n2026-03-30,Card fee,-2\n2026-04-02,Supplies,-40\n'


class Undo(Exception):
    pass


class DuplicateLineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='duplicate-owner')
        cls.other = get_user_model().objects.create_user(username='duplicate-other')
        Membership.objects.create(user=cls.user, installation=install, role='preparer')
        Membership.objects.create(user=cls.other, installation=install, role='administrator')
        cls.entity = Entity.objects.create(engagement=Engagement.objects.create(
            owner=cls.user, installation=install, name='Synthetic duplicates'), name='Synthetic duplicate business')

    def setUp(self):
        self.book = ledger.create_book(self.user, self.entity.pk, PERIOD)
        self.accounts = {a['code']: a['id'] for a in self.book['accounts']}
        savings = ledger.create_account(self.user, self.book['id'], {'expected_revision': 0, 'code': '1010', 'name': 'Savings', 'category': 'asset'})
        ledger.set_account_cash(self.user, self.book['id'], savings['id'], {'expected_revision': self.revision(), 'cash': True})
        self.accounts['1010'] = savings['id']
        self.load('march.csv', MARCH, '1000')
        self.load('late-march.csv', OVERLAP, '1000')

    def revision(self):
        return ledger.read_book(self.user, self.book['id'])['revision']

    def load(self, name, content, code=None):
        ledger.import_statement(self.user, self.book['id'], {'expected_revision': self.revision(), 'filename': name, 'content': content,
                                                            **({'account_id': self.accounts[code]} if code else {})})

    def rows(self, filename):
        return [row for row in ledger.source_rows(self.user, self.book['id'])['rows'] if row['import_filename'] == filename]

    def ask(self, row, of, user=None):
        return approvals.request_action(user or self.user, {'operation': 'statement.duplicate', 'summary': 'Mark the repeated line',
            'payload': {'book_id': self.book['id'], 'row_id': row['id'], 'of_row_id': of['id']}})

    def approve(self, item):
        return approvals.decide(self.user, item['id'], {'decision': 'approve', 'payload_hash': item['payload_hash']})

    def test_candidates_pair_each_repeated_line_with_one_earlier_line_and_leave_new_lines_alone(self):
        first, second = self.rows('march.csv'), self.rows('late-march.csv')
        found = ledger.match_candidates(self.user, self.book['id'])
        self.assertEqual(found['duplicates'], [{'row_id': second[0]['id'], 'of_row_id': first[1]['id']},      # Same payment, other capitals.
                                               {'row_id': second[1]['id'], 'of_row_id': first[2]['id']}])    # One of two fees repeats; the other is new.
        self.assertEqual((found['transfers'], found['entries']), ([], []))

    def test_approved_duplicate_settles_the_line_without_an_entry_and_keeps_it_out_of_further_work(self):
        original, repeat = self.rows('march.csv')[1], self.rows('late-march.csv')[0]
        item = self.ask(repeat, original)
        self.assertEqual(item['detail'], {'books': 'Synthetic duplicate business', 'currency': 'USD', 'account': '1000 Checking',
            'row': {'statement': 'late-march.csv', 'date': '2026-03-30', 'description': 'CLIENT PAYMENT', 'amount': '1250.5000'},
            'of': {'statement': 'march.csv', 'date': '2026-03-30', 'description': 'Client payment', 'amount': '1250.5000'}})
        self.assertEqual(SourceRowMatch.objects.count(), 0)
        before = self.revision()
        done = self.approve(item)
        self.assertEqual((done['state'], done['result']), ('approved', {'row_id': repeat['id'], 'of_row_id': original['id'], 'revision': before + 1}))
        now = next(r for r in self.rows('late-march.csv') if r['id'] == repeat['id'])
        self.assertEqual((now['status'], now['journal_id'], now['via'], now['duplicate_of']), ('duplicate', None, 'duplicate', original['id']))
        self.assertEqual(next(r for r in self.rows('march.csv') if r['id'] == original['id'])['duplicate_of'], None)
        event = LedgerEvent.objects.get(book_id=self.book['id'], revision=before + 1)
        self.assertEqual((event.action, str(event.target)), ('statement.duplicate', repeat['id']))
        with self.assertRaises(ledger.LedgerError) as refused:
            ledger.propose_journal(self.user, self.book['id'], {
                'operation_id': str(uuid.uuid4()), 'expected_revision': self.revision(), 'date': repeat['date'], 'description': 'Booked again',
                'source': {'kind': 'csv', 'reference': repeat['id']}, 'evidence': [repeat['id']],
                'lines': [{'account_id': self.accounts['1000'], 'debit': '1250.5', 'credit': '0'}, {'account_id': self.accounts['4000'], 'debit': '0', 'credit': '1250.5'}]})
        self.assertEqual(refused.exception.code, 'ledger_source_exists')
        self.assertNotIn(repeat['id'], json.dumps(ledger.match_candidates(self.user, self.book['id'])))
        self.assertEqual((Journal.objects.count(), ledger.verify_book(self.user, self.book['id'])['ok']), (0, True))
        self.assertEqual(self.approve(item)['result'], done['result'])
        self.assertEqual(SourceRowMatch.objects.count(), 1)

    def test_lines_that_are_not_the_same_movement_are_refused_and_nothing_is_recorded(self):
        first, second = self.rows('march.csv'), self.rows('late-march.csv')
        self.load('savings.csv', 'date,description,amount\n2026-03-30,Client payment,1250.5\n', '1010')
        self.load('loose.csv', 'date,description,amount\n2026-03-30,Client payment,1250.50\n')
        savings, loose = self.rows('savings.csv')[0], self.rows('loose.csv')[0]
        cases = ((second[3], first[0], 'duplicate_mismatch'),            # Other date and amount.
                 (second[0], first[2], 'duplicate_mismatch'),            # Same date, other amount.
                 (second[1], second[2], 'duplicate_same_statement'),     # Two lines of one statement are two movements.
                 (savings, first[1], 'duplicate_mismatch'),              # Same figures on another bank account.
                 (loose, first[1], 'match_account_required'),
                 (second[0], second[0], 'duplicate_mismatch'),
                 (second[0], {'id': str(uuid.uuid4())}, 'import_row_not_found'),
                 ({'id': 'row'}, first[1], 'import_row_not_found'))
        for row, of, code in cases:
            with self.assertRaises(approvals.ApprovalError, msg=code) as refused:
                self.ask(row, of)
            self.assertEqual(refused.exception.code, code)
        with self.assertRaises(approvals.ApprovalError):
            self.ask(second[0], first[1], self.other)
        with self.assertRaises(approvals.ApprovalError):
            approvals.request_action(self.user, {'operation': 'statement.duplicate', 'summary': 'x',
                'payload': {'book_id': self.book['id'], 'row_id': second[0]['id'], 'of_row_id': first[1]['id'], 'force': True}})
        self.assertEqual((ActionRequest.objects.count(), SourceRowMatch.objects.count()), (0, 0))

    def test_a_duplicate_cannot_be_repeated_chained_or_used_once_the_line_has_an_entry(self):
        first, second = self.rows('march.csv'), self.rows('late-march.csv')
        one, two = self.ask(second[0], first[1]), self.ask(second[0], first[1])
        self.approve(one)
        stale = self.approve(two)
        self.assertEqual((stale['state'], stale['error']), ('failed', 'match_row_not_pending'))
        with self.assertRaises(approvals.ApprovalError) as refused:
            self.ask(first[1], second[0])                                   # The original cannot become a duplicate of its own duplicate.
        self.assertEqual(refused.exception.code, 'duplicate_mismatch')
        ledger.propose_journal(self.user, self.book['id'], {
            'operation_id': str(uuid.uuid4()), 'expected_revision': self.revision(), 'date': second[1]['date'], 'description': 'Card fee',
            'source': {'kind': 'csv', 'reference': second[1]['id']}, 'evidence': [second[1]['id']],
            'lines': [{'account_id': self.accounts['6100'], 'debit': '2', 'credit': '0'}, {'account_id': self.accounts['1000'], 'debit': '0', 'credit': '2'}]})
        with self.assertRaises(approvals.ApprovalError) as refused:
            self.ask(second[1], first[2])                                   # Already has its own entry.
        self.assertEqual(refused.exception.code, 'match_row_not_pending')
        self.assertEqual(SourceRowMatch.objects.count(), 1)

    def test_the_integrity_check_finds_a_duplicate_mark_that_no_longer_fits(self):
        original, repeat = self.rows('march.csv')[1], self.rows('late-march.csv')[0]
        self.approve(self.ask(repeat, original))
        self.assertEqual(ledger.verify_book(self.user, self.book['id'])['ok'], True)
        found = []
        try:
            with transaction.atomic():
                SourceRowMatch.objects.filter(row_id=repeat['id']).update(duplicate_of_id=self.rows('march.csv')[0]['id'])
                found.append(ledger.verify_book(self.user, self.book['id']))
                raise Undo
        except Undo:
            pass
        self.assertEqual((found[0]['ok'], [(f['code'], f['journal_id']) for f in found[0]['findings']]), (False, [('row_match_invalid', None)]))

    def test_the_agent_imports_tool_lists_duplicate_candidates(self):
        from tests.test_bookkeeping_posting import BookkeepingPostingTests
        broker, _, _ = BookkeepingPostingTests.broker(self)
        answer = json.loads(broker.call('maipa_books_imports', {'book_id': self.book['id']})['content'][0]['text'])
        self.assertEqual(answer['duplicate_candidates'], ledger.match_candidates(self.user, self.book['id'])['duplicates'])
        self.assertEqual(len(answer['duplicate_candidates']), 2)
        self.assertEqual({row['duplicate_of'] for row in answer['rows']}, {None})
