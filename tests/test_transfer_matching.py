"""A transfer between two bank accounts is one entry: the second statement line is matched to it, never booked twice."""
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
CHECKING = ('date,description,amount\n2026-03-02,Transfer to savings,-500\n2026-03-05,Synthetic rent,-900\n'
            '2026-03-20,Transfer to savings,-500\n')
SAVINGS = 'date,description,amount\n2026-03-03,Transfer from checking,500\n2026-03-04,Interest,1.25\n2026-04-15,Late transfer in,500\n'


class Undo(Exception):
    pass


class TransferMatchingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='match-owner')
        cls.other = get_user_model().objects.create_user(username='match-other')
        Membership.objects.create(user=cls.user, installation=install, role='preparer')
        Membership.objects.create(user=cls.other, installation=install, role='administrator')
        cls.entity = Entity.objects.create(engagement=Engagement.objects.create(
            owner=cls.user, installation=install, name='Synthetic transfers'), name='Synthetic transfer business')

    def setUp(self):
        self.book = ledger.create_book(self.user, self.entity.pk, PERIOD)
        self.accounts = {a['code']: a['id'] for a in self.book['accounts']}
        savings = ledger.create_account(self.user, self.book['id'], {'expected_revision': 0, 'code': '1010', 'name': 'Savings', 'category': 'asset'})
        ledger.set_account_cash(self.user, self.book['id'], savings['id'], {'expected_revision': self.revision(), 'cash': True})
        self.accounts['1010'] = savings['id']
        self.load('checking.csv', CHECKING, '1000')
        self.load('savings.csv', SAVINGS, '1010')
        rows = ledger.source_rows(self.user, self.book['id'])['rows']
        self.rows = {(row['date'], row['description']): row for row in rows}
        self.out, self.into = self.rows[('2026-03-02', 'Transfer to savings')], self.rows[('2026-03-03', 'Transfer from checking')]

    def revision(self):
        return ledger.read_book(self.user, self.book['id'])['revision']

    def load(self, name, content, code):
        ledger.import_statement(self.user, self.book['id'], {'expected_revision': self.revision(), 'filename': name, 'content': content,
                                                            'account_id': self.accounts[code]})

    def entry(self, row, lines, post=False):
        made = ledger.propose_journal(self.user, self.book['id'], {
            'operation_id': str(uuid.uuid4()), 'expected_revision': self.revision(), 'date': row['date'], 'description': row['description'],
            'source': {'kind': 'csv', 'reference': row['id']}, 'evidence': [row['id']],
            'lines': [{'account_id': self.accounts[code], 'debit': debit, 'credit': credit} for code, debit, credit in lines]})
        if post:
            ledger.confirm_own_journal(self.user, self.book['id'], made['id'],
                {'expected_revision': self.revision(), 'payload_hash': made['payload_hash'], 'confirm': True})
        return made

    def transfer(self, post=False):
        return self.entry(self.out, [('1010', '500', '0'), ('1000', '0', '500')], post)

    def ask(self, row, journal, user=None):
        return approvals.request_action(user or self.user, {'operation': 'statement.match', 'summary': 'Match the savings line to the transfer',
            'payload': {'book_id': self.book['id'], 'row_id': row['id'], 'journal_id': journal['id']}})

    def status(self, row):
        current = next(r for r in ledger.source_rows(self.user, self.book['id'])['rows'] if r['id'] == row['id'])
        return current['status'], current['journal_id'], current['via']

    def test_candidates_pair_opposite_lines_of_two_bank_accounts_within_ten_days_and_lines_with_existing_entries(self):
        found = ledger.match_candidates(self.user, self.book['id'])
        self.assertEqual(found['transfers'], [
            {'out_row_id': self.out['id'], 'in_row_id': self.into['id'], 'amount': '500.0000', 'days_apart': 1}])  # The 20 March line is 17 days away.
        self.assertEqual(found['entries'], [])
        made = self.transfer()
        found = ledger.match_candidates(self.user, self.book['id'])
        self.assertEqual((found['transfers'], found['entries']), ([], [{'row_id': self.into['id'], 'journal_id': made['id'], 'days_apart': 1}]))

    def test_approved_match_links_the_second_line_to_the_one_transfer_entry_without_another_entry(self):
        made = self.transfer()
        self.assertEqual((self.status(self.out), self.status(self.into)), (('proposed', made['id'], 'entry'), ('pending', None, None)))
        item = self.ask(self.into, made)
        self.assertEqual(item['detail'], {'books': 'Synthetic transfer business', 'currency': 'USD',
            'row': {'date': '2026-03-03', 'description': 'Transfer from checking', 'amount': '500.0000', 'account': '1010 Savings'},
            'entry': {'sequence': made['sequence'], 'date': '2026-03-02', 'description': 'Transfer to savings', 'state': 'draft'},
            'lines': [{'account': '1010 Savings', 'debit': '500.0000', 'credit': '0.0000'}, {'account': '1000 Checking', 'debit': '0.0000', 'credit': '500.0000'}]})
        self.assertEqual((SourceRowMatch.objects.count(), self.status(self.into)[0]), (0, 'pending'))
        before = self.revision()
        done = approvals.decide(self.user, item['id'], {'decision': 'approve', 'payload_hash': item['payload_hash']})
        self.assertEqual((done['state'], done['result']), ('approved', {'row_id': self.into['id'], 'journal_id': made['id'], 'revision': before + 1}))
        self.assertEqual((self.status(self.into), Journal.objects.count()), (('proposed', made['id'], 'match'), 1))
        event = LedgerEvent.objects.get(book_id=self.book['id'], revision=before + 1)
        self.assertEqual((event.action, str(event.target)), ('statement.match', self.into['id']))
        ledger.confirm_own_journal(self.user, self.book['id'], made['id'], {'expected_revision': before + 1, 'payload_hash': made['payload_hash'], 'confirm': True})
        self.assertEqual((self.status(self.out)[0], self.status(self.into)[0]), ('posted', 'posted'))
        report = ledger.journal_preview(self.user, self.book['id'])
        balances = {a['code']: (a['balance_debit'], a['balance_credit']) for a in report['accounts'] if a['code'] in ('1000', '1010')}
        self.assertEqual(balances, {'1000': ('0.0000', '500.0000'), '1010': ('500.0000', '0.0000')})
        self.assertEqual(ledger.verify_book(self.user, self.book['id'])['ok'], True)
        self.assertEqual(approvals.decide(self.user, item['id'], {'decision': 'approve', 'payload_hash': item['payload_hash']})['result'], done['result'])
        self.assertEqual(SourceRowMatch.objects.count(), 1)

    def test_lines_that_do_not_fit_the_entry_are_refused_and_nothing_is_recorded(self):
        made = self.transfer()
        rent = self.entry(self.rows[('2026-03-05', 'Synthetic rent')], [('6200', '900', '0'), ('1000', '0', '900')])
        cases = ((self.rows[('2026-03-04', 'Interest')], made, 'import_account_mismatch'),            # Wrong amount.
                 (self.rows[('2026-03-20', 'Transfer to savings')], made, 'match_account_taken'),     # The entry's own checking side is already its source line.
                 (self.rows[('2026-04-15', 'Late transfer in')], made, 'match_date_too_far'),         # 44 days from the entry.
                 (self.out, made, 'match_row_not_pending'),                                           # Already the entry's own line.
                 (self.into, rent, 'import_account_mismatch'),                                        # Entry does not touch savings.
                 (self.into, {'id': str(uuid.uuid4())}, 'ledger_not_found'),
                 ({'id': str(uuid.uuid4())}, made, 'import_row_not_found'))
        for row, journal, code in cases:
            with self.assertRaises(approvals.ApprovalError, msg=code) as refused:
                self.ask(row, journal)
            self.assertEqual(refused.exception.code, code)
        with self.assertRaises(approvals.ApprovalError):
            self.ask(self.into, made, self.other)
        with self.assertRaises(approvals.ApprovalError):
            approvals.request_action(self.user, {'operation': 'statement.match', 'summary': 'x',
                'payload': {'book_id': self.book['id'], 'row_id': self.into['id'], 'journal_id': made['id'], 'force': True}})
        self.assertEqual((ActionRequest.objects.count(), SourceRowMatch.objects.count()), (0, 0))

    def test_a_matched_line_cannot_be_matched_or_booked_again_and_a_second_line_cannot_take_the_same_side(self):
        made = self.transfer()
        first, second = self.ask(self.into, made), self.ask(self.into, made)
        approvals.decide(self.user, first['id'], {'decision': 'approve', 'payload_hash': first['payload_hash']})
        stale = approvals.decide(self.user, second['id'], {'decision': 'approve', 'payload_hash': second['payload_hash']})
        self.assertEqual((stale['state'], stale['error']), ('failed', 'match_row_not_pending'))
        with self.assertRaises(ledger.LedgerError) as refused:
            self.entry(self.into, [('1010', '500', '0'), ('4000', '0', '500')])
        self.assertEqual(refused.exception.code, 'ledger_source_exists')
        self.load('savings-again.csv', 'date,description,amount\n2026-03-06,Second transfer in,500\n', '1010')
        again = next(r for r in ledger.source_rows(self.user, self.book['id'])['rows'] if r['description'] == 'Second transfer in')
        with self.assertRaises(approvals.ApprovalError) as refused:
            self.ask(again, made)
        self.assertEqual(refused.exception.code, 'match_account_taken')
        self.assertEqual((SourceRowMatch.objects.count(), Journal.objects.count()), (1, 1))

    def test_a_line_of_a_statement_without_a_bank_account_cannot_be_matched(self):
        ledger.import_statement(self.user, self.book['id'], {'expected_revision': self.revision(), 'filename': 'loose.csv',
                                                            'content': 'date,description,amount\n2026-03-03,Loose line,500\n'})
        loose = next(r for r in ledger.source_rows(self.user, self.book['id'])['rows'] if r['description'] == 'Loose line')
        with self.assertRaises(approvals.ApprovalError) as refused:
            self.ask(loose, self.transfer())
        self.assertEqual(refused.exception.code, 'match_account_required')

    def test_the_integrity_check_finds_a_match_whose_entry_no_longer_moves_the_line(self):
        made = self.transfer()
        item = self.ask(self.into, made)
        approvals.decide(self.user, item['id'], {'decision': 'approve', 'payload_hash': item['payload_hash']})
        self.assertEqual(ledger.verify_book(self.user, self.book['id'])['ok'], True)
        found = []
        try:
            with transaction.atomic():
                SourceRowMatch.objects.filter(row_id=self.into['id']).update(row_id=self.rows[('2026-03-04', 'Interest')]['id'])
                found.append(ledger.verify_book(self.user, self.book['id']))
                raise Undo
        except Undo:
            pass
        self.assertEqual((found[0]['ok'], [(f['code'], f['journal_id']) for f in found[0]['findings']]), (False, [('row_match_invalid', made['id'])]))

    def test_the_agent_imports_tool_adds_candidates_and_http_rows_report_how_each_line_was_settled(self):
        from tests.test_bookkeeping_posting import BookkeepingPostingTests
        made = self.transfer()
        self.client.force_login(self.user)
        listed = self.client.get(f"/api/books/{self.book['id']}/imports/", secure=True, HTTP_X_MAIPA_AGENT_ACTOR=str(self.user.pk)).json()
        self.assertEqual(set(listed), {'book_id', 'currency', 'revision', 'rows'})
        self.assertEqual({row['via'] for row in listed['rows']}, {'entry', None})
        broker, _, _ = BookkeepingPostingTests.broker(self)
        import json
        answer = json.loads(broker.call('maipa_books_imports', {'book_id': self.book['id']})['content'][0]['text'])
        self.assertEqual(answer['rows'], listed['rows'])
        self.assertEqual((answer['transfer_candidates'], answer['entry_candidates']),
                         ([], [{'row_id': self.into['id'], 'journal_id': made['id'], 'days_apart': 1}]))
