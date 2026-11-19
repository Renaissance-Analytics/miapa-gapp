"""A statement import names its bank account; entries for its rows must move that account by the exact row amount."""
import json
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.maipa.approvals import attachments, services as approvals
from backend.maipa.approvals.models import ActionRequest
from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.ledger import services as ledger
from backend.maipa.ledger.models import Journal, SourceImport, SourceRow
from backend.maipa.workspace.models import Installation, Membership

PERIOD = {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'}
CSV = 'date,description,amount\n2026-03-01,Synthetic client payment,1250.5\n2026-03-03,Synthetic rent,-900\n'


class ImportBankAccountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='bank-import-owner')
        cls.other = get_user_model().objects.create_user(username='bank-import-other')
        Membership.objects.create(user=cls.user, installation=install, role='preparer')
        Membership.objects.create(user=cls.other, installation=install, role='administrator')
        engagement = Engagement.objects.create(owner=cls.user, installation=install, name='Synthetic bank imports')
        cls.entity = Entity.objects.create(engagement=engagement, name='Synthetic bank import business')
        cls.second = Entity.objects.create(engagement=engagement, name='Synthetic other business')

    def setUp(self):
        self.book = ledger.create_book(self.user, self.entity.pk, PERIOD)
        self.accounts = {a['code']: a['id'] for a in self.book['accounts']}

    def revision(self):
        return ledger.read_book(self.user, self.book['id'])['revision']

    def load(self, content=CSV, **extra):
        return ledger.import_statement(self.user, self.book['id'], {'expected_revision': self.revision(), 'filename': 'synthetic-march.csv',
                                                                    'content': content, **extra})

    def rows(self):
        return {row['description']: row for row in ledger.source_rows(self.user, self.book['id'])['rows']}

    def propose(self, row, lines):
        return ledger.propose_journal(self.user, self.book['id'], {
            'operation_id': str(uuid.uuid4()), 'expected_revision': self.revision(), 'date': row['date'], 'description': row['description'],
            'source': {'kind': 'csv', 'reference': row['id']}, 'evidence': [row['id']],
            'lines': [{'account_id': self.accounts[code], 'debit': debit, 'credit': credit} for code, debit, credit in lines]})

    def test_an_import_records_its_bank_account_and_every_row_reports_it(self):
        saved = self.load(account_id=self.accounts['1000'])
        self.assertEqual((saved['created'], saved['account_id'], saved['row_count']), (True, self.accounts['1000'], 2))
        self.assertEqual({row['account_id'] for row in self.rows().values()}, {self.accounts['1000']})
        again = self.load(account_id=self.accounts['1000'])
        self.assertEqual((again['created'], again['id'], again['account_id']), (False, saved['id'], self.accounts['1000']))
        plain = self.load('date,description,amount\n2026-04-01,Synthetic other deposit,5\n')
        self.assertEqual(plain['account_id'], None)
        self.assertEqual(self.rows()['Synthetic other deposit']['account_id'], None)

    def test_only_a_marked_bank_or_cash_account_of_the_same_book_can_be_named(self):
        foreign = next(a['id'] for a in ledger.create_book(self.user, self.second.pk, PERIOD)['accounts'] if a['code'] == '1000')
        for account in (self.accounts['1500'], self.accounts['2000'], foreign, str(uuid.uuid4()), 'checking', None, 1):
            with self.assertRaises(ledger.LedgerError, msg=account) as refused:
                self.load(account_id=account)
            if account in (self.accounts['1500'], self.accounts['2000'], foreign):
                self.assertEqual(refused.exception.code, 'import_account_invalid')
        self.assertEqual((SourceImport.objects.count(), SourceRow.objects.count(), self.revision()), (0, 0, 0))

    def test_entries_for_bank_rows_must_move_that_bank_account_by_the_exact_amount_on_the_right_side(self):
        self.load(account_id=self.accounts['1000'])
        deposit, rent = self.rows()['Synthetic client payment'], self.rows()['Synthetic rent']
        for lines in ([('1100', '1250.5', '0'), ('4000', '0', '1250.5')],                       # Bank account untouched.
                      [('4000', '1250.5', '0'), ('1000', '0', '1250.5')],                       # Money in recorded as money out.
                      [('1000', '1000', '0'), ('1100', '250.5', '0'), ('4000', '0', '1250.5')],  # Only part reaches the bank.
                      [('1000', '1250.5', '0'), ('6100', '10', '0'), ('1000', '0', '10'), ('4000', '0', '1250.5')]):  # Bank on both sides.
            with self.assertRaises(ledger.LedgerError, msg=lines) as refused:
                self.propose(deposit, lines)
            self.assertIn(refused.exception.code, ('import_account_mismatch', 'import_amount_mismatch'))
        with self.assertRaises(ledger.LedgerError) as refused:
            self.propose(rent, [('1000', '900', '0'), ('6200', '0', '900')])
        self.assertEqual(refused.exception.code, 'import_account_mismatch')
        self.assertEqual(Journal.objects.count(), 0)
        self.assertEqual(self.propose(deposit, [('1000', '1250.5', '0'), ('4000', '0', '1000'), ('2100', '0', '250.5')])['state'], 'draft')
        self.assertEqual(self.propose(rent, [('6200', '900', '0'), ('1000', '0', '900')])['state'], 'draft')
        self.assertEqual({row['status'] for row in self.rows().values()}, {'proposed'})

    def test_rows_of_an_import_without_a_bank_account_keep_the_amount_rule_only(self):
        self.load()
        deposit = self.rows()['Synthetic client payment']
        self.assertEqual(self.propose(deposit, [('1100', '1250.5', '0'), ('4000', '0', '1250.5')])['state'], 'draft')

    def ask(self, payload, user=None):
        return approvals.request_action(user or self.user, {'operation': 'statement.import', 'summary': 'Import the March statement', 'payload': payload})

    def test_approved_import_request_carries_the_named_bank_account_and_shows_it_for_review(self):
        item = self.ask({'book_id': self.book['id'], 'filename': 'synthetic-march.csv', 'content': CSV, 'account_id': self.accounts['1000']})
        self.assertEqual(item['detail'], {'books': 'Synthetic bank import business', 'currency': 'USD', 'account': '1000 Checking'})
        self.assertEqual((item['payload']['account_id'], SourceImport.objects.count()), (self.accounts['1000'], 0))
        done = approvals.decide(self.user, item['id'], {'decision': 'approve', 'payload_hash': item['payload_hash']})
        self.assertEqual((done['state'], done['result']['account_id'], done['result']['row_count']), ('approved', self.accounts['1000'], 2))
        self.assertEqual(str(SourceImport.objects.get().account_id), self.accounts['1000'])
        plain = self.ask({'book_id': self.book['id'], 'filename': 'synthetic-april.csv', 'content': 'date,description,amount\n2026-04-01,Synthetic other deposit,5\n'})
        self.assertEqual(plain['detail'], {'books': 'Synthetic bank import business', 'currency': 'USD', 'account': None})

    def test_import_requests_naming_an_unmarked_foreign_or_malformed_account_are_refused_and_not_recorded(self):
        for account in (self.accounts['1500'], str(uuid.uuid4()), 'checking', None):
            with self.assertRaises(approvals.ApprovalError, msg=account):
                self.ask({'book_id': self.book['id'], 'filename': 'synthetic-march.csv', 'content': CSV, 'account_id': account})
        with self.assertRaises(approvals.ApprovalError):
            self.ask({'book_id': self.book['id'], 'filename': 'synthetic-march.csv', 'content': CSV, 'account_id': self.accounts['1000'], 'post': True})
        self.assertEqual((ActionRequest.objects.count(), SourceImport.objects.count()), (0, 0))

    def test_an_attached_statement_resolves_with_its_bank_account_and_refuses_other_extras(self):
        saved = attachments.store(self.user, {'name': 'synthetic-march.csv', 'content': CSV})
        resolved = attachments.resolve_payload(self.user, 'statement.import', {'book_id': self.book['id'], 'attachment_id': saved['id'], 'account_id': self.accounts['1000']})
        self.assertEqual(resolved, {'book_id': self.book['id'], 'filename': 'synthetic-march.csv', 'content': CSV, 'account_id': self.accounts['1000']})
        self.assertEqual(attachments.resolve_payload(self.user, 'statement.import', {'book_id': self.book['id'], 'attachment_id': saved['id']}),
                         {'book_id': self.book['id'], 'filename': 'synthetic-march.csv', 'content': CSV})
        with self.assertRaises(Exception):
            attachments.resolve_payload(self.user, 'statement.import', {'book_id': self.book['id'], 'attachment_id': saved['id'], 'post': True})

    def test_http_import_passes_the_bank_account_and_rows_report_it(self):
        self.client.force_login(self.user)
        actor = {'HTTP_X_MAIPA_AGENT_ACTOR': str(self.user.pk)}
        url = f"/api/books/{self.book['id']}/imports/"
        bad = self.client.post(url, data=json.dumps({'expected_revision': 0, 'filename': 'synthetic-march.csv', 'content': CSV, 'account_id': self.accounts['1500']}),
                               content_type='application/json', secure=True, **actor)
        self.assertEqual((bad.status_code, bad.json()['error']['code'], SourceImport.objects.count()), (400, 'import_account_invalid', 0))
        made = self.client.post(url, data=json.dumps({'expected_revision': 0, 'filename': 'synthetic-march.csv', 'content': CSV, 'account_id': self.accounts['1000']}),
                                content_type='application/json', secure=True, **actor)
        self.assertEqual((made.status_code, made.json()['account_id']), (201, self.accounts['1000']))
        listed = self.client.get(url, secure=True, **actor).json()
        self.assertEqual({row['account_id'] for row in listed['rows']}, {self.accounts['1000']})
