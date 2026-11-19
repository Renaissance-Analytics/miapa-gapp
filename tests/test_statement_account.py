"""A statement imported without a bank account can be tied to one later, by approval, when its entries agree."""
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.maipa.approvals import services as approvals
from backend.maipa.approvals.models import ActionRequest
from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.ledger import services as ledger
from backend.maipa.ledger.models import LedgerEvent, SourceImport
from backend.maipa.workspace.models import Installation, Membership

PERIOD = {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'}
CSV = 'date,description,amount\n2026-04-04,Software subscription,-49\n2026-04-06,Client payment,800\n'


class StatementAccountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='tie-owner')
        cls.other = get_user_model().objects.create_user(username='tie-other')
        Membership.objects.create(user=cls.user, installation=install, role='preparer')
        Membership.objects.create(user=cls.other, installation=install, role='administrator')
        engagement = Engagement.objects.create(owner=cls.user, installation=install, name='Synthetic ties')
        cls.entity = Entity.objects.create(engagement=engagement, name='Synthetic tie business')
        cls.second = Entity.objects.create(engagement=engagement, name='Synthetic other business')

    def setUp(self):
        self.book = ledger.create_book(self.user, self.entity.pk, PERIOD)
        self.accounts = {a['code']: a['id'] for a in self.book['accounts']}
        self.statement = ledger.import_statement(self.user, self.book['id'], {'expected_revision': 0, 'filename': 'april.csv', 'content': CSV})
        self.rows = {row['description']: row for row in ledger.source_rows(self.user, self.book['id'])['rows']}

    def revision(self):
        return ledger.read_book(self.user, self.book['id'])['revision']

    def entry(self, row, lines, description=None, cite=True):
        reference = row['id'] if cite else f'synthetic-{uuid.uuid4()}'
        made = ledger.propose_journal(self.user, self.book['id'], {
            'operation_id': str(uuid.uuid4()), 'expected_revision': self.revision(), 'date': row['date'], 'description': description or row['description'],
            'source': {'kind': 'csv' if cite else 'manual', 'reference': reference}, 'evidence': [reference],
            'lines': [{'account_id': self.accounts[code], 'debit': debit, 'credit': credit} for code, debit, credit in lines]})
        ledger.confirm_own_journal(self.user, self.book['id'], made['id'],
            {'expected_revision': self.revision(), 'payload_hash': made['payload_hash'], 'confirm': True})
        return made

    def ask(self, user=None, **changes):
        payload = {'book_id': self.book['id'], 'import_id': self.statement['id'], 'account_id': self.accounts['1000']} | changes
        return approvals.request_action(user or self.user, {'operation': 'statement.account', 'summary': 'Tie april.csv to Checking', 'payload': payload})

    def approve(self, item):
        return approvals.decide(self.user, item['id'], {'decision': 'approve', 'payload_hash': item['payload_hash']})

    def test_approved_request_ties_the_statement_and_its_lines_to_the_bank_account(self):
        self.entry(self.rows['Client payment'], [('1000', '800', '0'), ('4000', '0', '800')])
        item = self.ask()
        self.assertEqual(item['detail'], {'books': 'Synthetic tie business', 'statement': 'april.csv', 'row_count': 2, 'account': '1000 Checking', 'current': True})
        self.assertEqual({row['account_id'] for row in ledger.source_rows(self.user, self.book['id'])['rows']}, {None})
        before = self.revision()
        done = self.approve(item)
        self.assertEqual((done['state'], done['result']['account_id'], done['result']['id']), ('approved', self.accounts['1000'], self.statement['id']))
        self.assertEqual({row['account_id'] for row in ledger.source_rows(self.user, self.book['id'])['rows']}, {self.accounts['1000']})
        event = LedgerEvent.objects.get(book_id=self.book['id'], revision=before + 1)
        self.assertEqual((event.action, str(event.target)), ('statement.account', self.statement['id']))
        self.assertEqual(self.approve(item)['result'], done['result'])
        self.assertEqual((self.revision(), ledger.verify_book(self.user, self.book['id'])['ok']), (before + 1, True))

    def test_a_statement_whose_entries_do_not_move_that_account_or_that_is_already_tied_is_refused(self):
        self.entry(self.rows['Client payment'], [('1100', '800', '0'), ('4000', '0', '800')])     # Booked to receivables, not the bank.
        with self.assertRaises(approvals.ApprovalError) as refused:
            self.ask()
        self.assertEqual(refused.exception.code, 'import_account_mismatch')
        foreign = next(a['id'] for a in ledger.create_book(self.user, self.second.pk, PERIOD)['accounts'] if a['code'] == '1000')
        for changes, code in (({'account_id': self.accounts['1500']}, 'import_account_invalid'), ({'account_id': foreign}, 'import_account_invalid'),
                              ({'import_id': str(uuid.uuid4())}, 'import_not_found'), ({'import_id': 'april'}, 'import_not_found'),
                              ({'account_id': 'checking'}, 'import_account_invalid')):
            with self.assertRaises(approvals.ApprovalError, msg=changes) as refused:
                self.ask(**changes)
            self.assertEqual(refused.exception.code, code, changes)
        with self.assertRaises(approvals.ApprovalError):
            self.ask(self.other)
        with self.assertRaises(approvals.ApprovalError):
            self.ask(force=True)
        self.assertEqual((ActionRequest.objects.count(), SourceImport.objects.get().account_id), (0, None))

    def test_a_tied_statement_cannot_be_tied_again_and_a_stale_request_fails(self):
        first, second = self.ask(), self.ask()
        self.approve(first)
        stale = self.approve(second)
        self.assertEqual((stale['state'], stale['error']), ('failed', 'import_account_already_set'))
        with self.assertRaises(approvals.ApprovalError) as refused:
            self.ask()
        self.assertEqual(refused.exception.code, 'import_account_already_set')

    def test_tying_the_statement_lets_a_line_whose_entry_was_reversed_be_matched_to_a_corrected_entry(self):
        line = self.rows['Software subscription']
        wrong = self.entry(line, [('6100', '49', '0'), ('1000', '0', '49')])
        ledger.reverse_journal(self.user, self.book['id'], wrong['id'], {'expected_revision': self.revision(), 'date': '2026-04-30'})
        right = self.entry(line, [('6200', '49', '0'), ('1000', '0', '49')], 'Software subscription, corrected', cite=False)
        payload = {'book_id': self.book['id'], 'row_id': line['id'], 'journal_id': right['id']}
        with self.assertRaises(approvals.ApprovalError) as refused:
            approvals.request_action(self.user, {'operation': 'statement.match', 'summary': 'Match', 'payload': payload})
        self.assertEqual(refused.exception.code, 'match_account_required')
        self.approve(self.ask())                                                    # The reversed entry does not block tying.
        match = approvals.request_action(self.user, {'operation': 'statement.match', 'summary': 'Match', 'payload': payload})
        self.assertEqual(self.approve(match)['state'], 'approved')
        now = next(r for r in ledger.source_rows(self.user, self.book['id'])['rows'] if r['id'] == line['id'])
        self.assertEqual((now['status'], now['via'], now['journal_id']), ('posted', 'match', right['id']))
        self.assertEqual(ledger.verify_book(self.user, self.book['id'])['ok'], True)
