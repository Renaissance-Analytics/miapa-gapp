"""Own-book posting choice: exact human confirmation, never agent self-approval."""
import json
import uuid

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from backend.maipa.ledger import services
from backend.maipa.ledger.models import Book, Journal, LedgerEvent
from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.workspace.models import Installation, Membership


class BookkeepingPostingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='bookkeeper', password='synthetic-books-password')
        cls.other = get_user_model().objects.create_user(username='other-bookkeeper')
        cls.member = Membership.objects.create(user=cls.user, installation=install, role='preparer')
        Membership.objects.create(user=cls.other, installation=install, role='administrator')
        engagement = Engagement.objects.create(owner=cls.user, installation=install, name='Synthetic bookkeeping')
        cls.entity = Entity.objects.create(engagement=engagement, name='Synthetic own business')

    def setUp(self):
        self.book = services.create_book(self.user, self.entity.pk,
            {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'})
        self.accounts = {a['code']: a['id'] for a in self.book['accounts']}
        self.value = {'operation_id': str(uuid.uuid4()), 'expected_revision': 0,
            'date': '2026-11-04', 'description': 'Synthetic office purchase',
            'source': {'kind': 'manual', 'reference': 'synthetic-receipt-42'}, 'evidence': ['synthetic-receipt-42'],
            'lines': [{'account_id': self.accounts['6200'], 'debit': '42.17', 'credit': '0'},
                      {'account_id': self.accounts['1000'], 'debit': '0', 'credit': '42.17'}]}
        self.entry = services.propose_journal(self.user, self.book['id'], self.value)
        self.client = Client(enforce_csrf_checks=True)
        csrf = self.client.get('/api/session/csrf/', secure=True).json()['csrf_token']
        login = self.client.post('/api/session/login/', data=json.dumps({'username': 'bookkeeper',
            'password': 'synthetic-books-password'}), content_type='application/json', secure=True,
            HTTP_ORIGIN='https://testserver', HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(login.status_code, 200)
        self.csrf = login.json()['csrf_token']
        self.url = f"/api/books/{self.book['id']}/journals/{self.entry['id']}/post/"

    def confirmation(self):
        return {'expected_revision': 1, 'payload_hash': self.entry['payload_hash'], 'confirm': True}

    def post(self, value=None, token=True):
        return self.client.post(self.url, data=json.dumps(value or self.confirmation()),
            content_type='application/json', secure=True, HTTP_ORIGIN='https://testserver',
            HTTP_X_CSRFTOKEN=self.csrf if token else '', HTTP_X_MAIPA_AGENT_ACTOR=str(self.user.pk))

    def test_owner_human_confirmation_posts_exact_entry_and_durable_trial_balance(self):
        response = self.post()
        self.assertEqual(response.status_code, 200)
        receipt = response.json()
        self.assertEqual((receipt['state'], receipt['journal_id'], receipt['payload_hash'], receipt['actor_id']),
                         ('posted', self.entry['id'], self.entry['payload_hash'], str(self.user.pk)))
        report = services.journal_preview(self.user, self.book['id'])
        rows = {a['code']: a for a in report['accounts']}
        self.assertEqual((rows['6200']['posted_debit'], rows['1000']['posted_credit']), ('42.1700', '42.1700'))
        self.assertEqual((report['posted_debit'], report['posted_credit'], report['pending_count']), ('42.1700', '42.1700', 0))
        self.assertEqual(list(LedgerEvent.objects.order_by('revision').values_list('action', flat=True)),
                         ['journal.propose', 'journal.post'])

    def test_exact_post_retry_returns_original_receipt_without_posting_again(self):
        first = self.post(); again = self.post()
        self.assertEqual((first.status_code, again.status_code), (200, 200))
        self.assertEqual(first.json(), again.json())
        self.assertEqual((Book.objects.get().revision, LedgerEvent.objects.count()), (2, 2))

    def test_different_hash_stale_revision_missing_confirm_and_actor_hints_cannot_post(self):
        for change in ({'payload_hash': '0'*64}, {'expected_revision': 0}, {'confirm': False},
                       {'actor_id': str(self.user.pk)}, {'authorization': 'agent-says-approved'}):
            with self.subTest(change=change):
                self.assertIn(self.post(self.confirmation() | change).status_code, (400, 409))
        self.assertEqual((Journal.objects.get().state, Book.objects.get().revision), ('draft', 1))

    def test_missing_csrf_revoked_role_and_foreign_owner_refuse_posting(self):
        self.assertEqual(self.post(token=False).status_code, 403)
        Membership.objects.filter(pk=self.member.pk).update(role='viewer')
        self.assertIn(self.post().status_code, (403, 404))
        Membership.objects.filter(pk=self.member.pk).update(role='preparer')
        self.client.force_login(self.other)
        self.assertIn(self.post().status_code, (403, 404, 409))
        self.assertEqual((Journal.objects.get().state, LedgerEvent.objects.count()), ('draft', 1))

    def test_tampered_persisted_lines_are_not_confirmed_using_the_old_proposal_hash(self):
        from backend.maipa.ledger.models import JournalLine
        JournalLine.objects.filter(journal_id=self.entry['id'], debit_units__gt=0).update(debit_units=421800)
        self.assertIn(self.post().status_code, (400, 409))
        self.assertEqual((Journal.objects.get().state, Book.objects.get().revision), ('draft', 1))

    def test_http_reads_are_original_actor_bound_and_do_not_expose_foreign_books(self):
        headers = {'HTTP_X_MAIPA_AGENT_ACTOR': str(self.user.pk)}
        response = self.client.get('/api/books/', secure=True, **headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['books'][0]['id'], self.book['id'])
        current = self.client.get(f"/api/books/{self.book['id']}/", secure=True, **headers)
        self.assertEqual((current.status_code, current['Cache-Control']), (200, 'no-store'))
        self.assertEqual(current.json()['book']['journals'][0]['state'], 'draft')
        stale = self.client.get('/api/books/', secure=True, HTTP_X_MAIPA_AGENT_ACTOR=str(self.other.pk))
        self.assertEqual(stale.status_code, 409)

    def test_agent_ledger_tools_have_proposal_and_read_but_no_confirmation_or_posting_tool(self):
        from backend.maipa.ledger.tools import tool_definitions
        definitions = tool_definitions()
        self.assertEqual({t['name'] for t in definitions}, {'maipa_books_list', 'maipa_books_read', 'maipa_books_activity', 'maipa_books_imports', 'maipa_journal_propose'})
        schema = next(t for t in definitions if t['name'] == 'maipa_journal_propose')['inputSchema']
        self.assertFalse(schema['additionalProperties'])
        self.assertNotIn('confirm', json.dumps(schema))
        self.assertNotIn('actor_id', json.dumps(schema))

    def broker(self):
        from pathlib import Path
        import tempfile
        from django.test import override_settings
        from backend.maipa.storage import prepare_directory, prepare_files
        from backend.maipa.agent_accounts.models import AgentAccount
        from backend.maipa.agent_accounts.references import record_session
        from backend.maipa.agent_accounts import ui
        from backend.maipa.ledger.tools import LedgerBroker
        temporary = tempfile.TemporaryDirectory(prefix='maipa-books-tools-')
        self.addCleanup(temporary.cleanup)
        data = Path(temporary.name) / 'install'
        prepare_directory(data); prepare_files(data)
        setting = override_settings(DATA_ROOT=data); setting.enable(); self.addCleanup(setting.disable)
        account = AgentAccount.objects.create(owner=self.user, provider='claude', label='Synthetic books connection')
        session = record_session(self.user, str(account.pk), 'synthetic-native-books-handle')
        lease = ui.open_ui_lease(self.user, str(account.pk), session)
        ui.dispatch_ui(self.user, str(account.pk), lease, {'method': 'tools/call', 'params': {
            'name': 'maipa_ui_present', 'arguments': {'revision': 0, 'title': 'Own books',
                'schema': {'id': 'owned-books', 'type': 'Maipa.Books', 'props': {'book_id': self.book['id']}}}}})
        return LedgerBroker(self.user, str(account.pk), lease), account, lease

    def test_real_original_session_agent_reads_and_proposes_but_cannot_post(self):
        broker, _, _ = self.broker()
        read = broker.call('maipa_books_read', {'book_id': self.book['id']})
        self.assertFalse(read.get('isError', False))
        self.assertEqual(json.loads(read['content'][0]['text'])['report']['pending_count'], 1)
        value = self.value | {'operation_id': str(uuid.uuid4()), 'expected_revision': 1,
            'source': {'kind': 'manual', 'reference': 'synthetic-second-receipt'}}
        response = broker.call('maipa_journal_propose', {'book_id': self.book['id'], 'entry': value})
        self.assertFalse(response.get('isError', False))
        self.assertEqual(json.loads(response['content'][0]['text'])['state'], 'draft')
        refused = broker.call('maipa_journal_post', {'book_id': self.book['id'], 'confirm': True})
        self.assertTrue(refused['isError'])
        self.assertEqual((Journal.objects.filter(state='posted').count(), Journal.objects.count()), (0, 2))

    def test_dismissed_component_and_revoked_original_session_authority_prevent_agent_mutation(self):
        broker, account, lease = self.broker()
        self.assertFalse(broker.call('maipa_books_read', {'book_id': self.book['id']}).get('isError', False))
        from backend.maipa.agent_accounts import ui
        ui.dispatch_ui(self.user, str(account.pk), lease, {'method': 'tools/call', 'params': {
            'name': 'maipa_ui_dismiss', 'arguments': {'revision': 1}}})
        value = self.value | {'operation_id': str(uuid.uuid4()), 'expected_revision': 1,
            'source': {'kind': 'manual', 'reference': 'must-not-be-recorded'}}
        self.assertTrue(broker.call('maipa_journal_propose', {'book_id': self.book['id'], 'entry': value})['isError'])
        Membership.objects.filter(pk=self.member.pk).update(enabled=False)
        self.assertTrue(broker.call('maipa_books_read', {'book_id': self.book['id']})['isError'])
        self.assertEqual((Journal.objects.count(), Book.objects.get().revision), (1, 1))

    def test_agent_reads_imported_rows_and_proposes_a_cited_entry_but_cannot_import(self):
        services.import_statement(self.user, self.book['id'], {'expected_revision': 1, 'filename': 'synthetic-bank.csv',
            'content': 'date,description,amount\n2026-11-04,Synthetic bank fee,-12.5\n'})
        broker, _, _ = self.broker()
        rows = json.loads(broker.call('maipa_books_imports', {'book_id': self.book['id']})['content'][0]['text'])['rows']
        self.assertEqual([(r['amount'], r['status']) for r in rows], [('-12.5000', 'pending')])
        read = json.loads(broker.call('maipa_books_read', {'book_id': self.book['id']})['content'][0]['text'])
        value = {'operation_id': str(uuid.uuid4()), 'expected_revision': read['book']['revision'], 'date': rows[0]['date'],
                 'description': rows[0]['description'], 'source': {'kind': 'csv', 'reference': rows[0]['id']},
                 'evidence': ['synthetic-bank.csv row 1'],
                 'lines': [{'account_id': self.accounts['6100'], 'debit': '12.5', 'credit': '0'},
                           {'account_id': self.accounts['1000'], 'debit': '0', 'credit': '12.5'}]}
        response = broker.call('maipa_journal_propose', {'book_id': self.book['id'], 'entry': value})
        self.assertFalse(response.get('isError', False))
        self.assertEqual(json.loads(response['content'][0]['text'])['state'], 'draft')
        self.assertEqual(services.source_rows(self.user, self.book['id'])['rows'][0]['status'], 'proposed')
        refused = broker.call('maipa_books_import', {'book_id': self.book['id'], 'content': 'date,description,amount\n'})
        self.assertTrue(refused['isError'])
        self.assertEqual(Journal.objects.filter(state='posted').count(), 0)
