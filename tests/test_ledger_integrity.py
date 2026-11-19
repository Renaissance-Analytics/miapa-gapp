"""Independent double-entry integrity check: clean books pass; every direct tampering is found."""
import uuid

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase

from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.ledger import services
from backend.maipa.ledger.models import Account, Journal, JournalLine, LedgerEvent, PostingReceipt
from backend.maipa.workspace.models import Installation, Membership


class Undo(Exception):
    pass


class LedgerIntegrityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='integrity-owner')
        cls.other = get_user_model().objects.create_user(username='integrity-other')
        Membership.objects.create(user=cls.user, installation=install, role='preparer')
        Membership.objects.create(user=cls.other, installation=install, role='administrator')
        engagement = Engagement.objects.create(owner=cls.user, installation=install, name='Synthetic integrity')
        cls.entity = Entity.objects.create(engagement=engagement, name='Synthetic integrity business')
        cls.second = Entity.objects.create(engagement=engagement, name='Synthetic second business')

    def setUp(self):
        period = {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'}
        self.book = services.create_book(self.user, self.entity.pk, period)
        self.foreign = services.create_book(self.user, self.second.pk, period)
        self.accounts = {a['code']: a['id'] for a in self.book['accounts']}
        self.sale = self.entry('2026-02-10', '1000', '4000', '1250.5')
        self.fee = self.entry('2026-03-05', '6100', '1000', '0.1')
        self.draft = self.entry('2026-03-25', '6200', '1000', '999', post=False)
        revision = services.read_book(self.user, self.book['id'])['revision']
        self.reversal = services.reverse_journal(self.user, self.book['id'], self.fee, {'expected_revision': revision, 'date': '2026-03-31'})['journal_id']
        services.import_statement(self.user, self.book['id'], {'expected_revision': revision + 2, 'filename': 'synthetic.csv',
            'content': 'date,description,amount\n2026-04-01,Synthetic deposit,12.5\n'})

    def entry(self, day, debit, credit, amount, post=True):
        revision = services.read_book(self.user, self.book['id'])['revision']
        reference = f'synthetic-{uuid.uuid4()}'
        made = services.propose_journal(self.user, self.book['id'], {
            'operation_id': str(uuid.uuid4()), 'expected_revision': revision, 'date': day, 'description': 'Synthetic entry',
            'source': {'kind': 'manual', 'reference': reference}, 'evidence': [reference],
            'lines': [{'account_id': self.accounts[debit], 'debit': amount, 'credit': '0'}, {'account_id': self.accounts[credit], 'debit': '0', 'credit': amount}]})
        if post:
            services.confirm_own_journal(self.user, self.book['id'], made['id'],
                {'expected_revision': revision + 1, 'payload_hash': made['payload_hash'], 'confirm': True})
        return made['id']

    def check(self):
        return services.verify_book(self.user, self.book['id'])

    def tampered(self, change):
        """Apply a direct database change, read the findings, then roll the change back."""
        found = []
        try:
            with transaction.atomic():
                change()
                found.append(self.check())
                raise Undo
        except Undo:
            pass
        return found[0]

    def codes(self, change, journal):
        result = self.tampered(change)
        self.assertFalse(result['ok'])
        return {item['code'] for item in result['findings'] if item['journal_id'] == journal}

    def test_clean_books_with_posted_draft_reversed_and_imported_records_pass_exactly(self):
        result = self.check()
        self.assertEqual(result, {'book_id': self.book['id'], 'revision': services.read_book(self.user, self.book['id'])['revision'],
            'ok': True, 'journals_checked': 4, 'posted_count': 3, 'draft_count': 1,
            'posted_debit': '1250.7000', 'posted_credit': '1250.7000', 'findings': []})
        self.assertEqual(self.check(), result)

    def test_changed_removed_or_foreign_lines_are_found_on_the_exact_entry(self):
        first = JournalLine.objects.filter(journal_id=self.sale, position=0)
        self.assertEqual(self.codes(lambda: first.update(debit_units=1), self.sale), {'journal_unbalanced', 'journal_hash_mismatch'})
        self.assertEqual(self.codes(lambda: first.delete(), self.sale), {'journal_lines_invalid', 'journal_unbalanced', 'journal_hash_mismatch'})
        elsewhere = Account.objects.get(book_id=self.foreign['id'], code='1000')
        self.assertEqual(self.codes(lambda: first.update(account=elsewhere), self.sale), {'line_account_foreign', 'journal_hash_mismatch'})
        self.assertEqual(self.codes(lambda: Journal.objects.filter(pk=self.sale).update(date='2026-02-11'), self.sale), {'journal_hash_mismatch'})
        self.assertEqual(self.codes(lambda: Journal.objects.filter(pk=self.sale).update(total_units=1), self.sale), {'journal_unbalanced'})
        self.assertEqual(self.check()['ok'], True)

    def test_posting_without_a_receipt_a_broken_reversal_and_a_missing_event_are_found(self):
        self.assertEqual(self.codes(lambda: Journal.objects.filter(pk=self.draft).update(state='posted'), self.draft), {'posting_receipt_invalid'})
        self.assertEqual(self.codes(lambda: PostingReceipt.objects.filter(journal_id=self.sale).delete(), self.sale), {'posting_receipt_invalid'})
        self.assertEqual(self.codes(lambda: PostingReceipt.objects.filter(journal_id=self.sale).update(payload_hash='0' * 64), self.sale), {'posting_receipt_invalid'})
        self.assertEqual(self.codes(lambda: Journal.objects.filter(pk=self.fee).update(state='draft'), self.fee), {'posting_receipt_invalid'})
        self.assertIn('reversal_invalid', self.codes(lambda: Journal.objects.filter(pk=self.fee).update(state='draft'), self.reversal))
        doubled = JournalLine.objects.filter(journal_id=self.reversal)
        self.assertEqual(self.codes(lambda: [doubled.filter(debit_units__gt=0).update(debit_units=2000), doubled.filter(credit_units__gt=0).update(credit_units=2000),
                                             Journal.objects.filter(pk=self.reversal).update(total_units=2000)], self.reversal),
                         {'reversal_invalid', 'journal_hash_mismatch'})
        missing = self.tampered(lambda: LedgerEvent.objects.filter(book_id=self.book['id'], revision=2).delete())
        self.assertEqual((missing['ok'], missing['findings']), (False, [{'code': 'event_history_incomplete', 'journal_id': None, 'sequence': None}]))

    def test_foreign_readers_and_malformed_books_are_refused(self):
        for user, book in ((self.other, self.book['id']), (self.user, str(uuid.uuid4())), (self.user, 'books')):
            with self.assertRaises(services.LedgerError):
                services.verify_book(user, book)

    def test_http_integrity_is_owner_bound_and_matches_the_service(self):
        url = f"/api/books/{self.book['id']}/integrity/"
        self.client.force_login(self.user)
        actor = {'HTTP_X_MAIPA_AGENT_ACTOR': str(self.user.pk)}
        self.assertEqual(self.client.get(url, secure=True).status_code, 409)
        response = self.client.get(url, secure=True, **actor)
        self.assertEqual((response.status_code, response.json()), (200, self.check()))
        self.assertEqual(self.client.post(url, data='{}', content_type='application/json', secure=True, **actor).status_code, 405)
        self.assertEqual(self.client.get(url + '?deep=1', secure=True, **actor).status_code, 400)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url, secure=True, HTTP_X_MAIPA_AGENT_ACTOR=str(self.other.pk)).status_code, 404)
