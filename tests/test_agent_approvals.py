"""The agent requests record changes; only the person's approval executes exactly what was requested."""
import json
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.maipa.approvals import services as approvals
from backend.maipa.approvals.models import ActionRequest
from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.ledger import services as ledger
from backend.maipa.ledger.models import Book, Journal, SourceRow
from backend.maipa.workspace.models import Installation, Membership

PERIOD = {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'}
CSV = 'date,description,amount\n2026-03-01,Synthetic client payment,1250.5\n2026-03-03,Synthetic rent,-900\n'


class AgentApprovalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='approval-owner')
        cls.other = get_user_model().objects.create_user(username='approval-other')
        cls.viewer = get_user_model().objects.create_user(username='approval-viewer')
        Membership.objects.create(user=cls.user, installation=install, role='preparer')
        Membership.objects.create(user=cls.other, installation=install, role='administrator')
        Membership.objects.create(user=cls.viewer, installation=install, role='viewer')
        engagement = Engagement.objects.create(owner=cls.user, installation=install, name='Synthetic approvals')
        cls.entity = Entity.objects.create(engagement=engagement, name='Synthetic approval business')

    def request(self, operation, payload, summary='Synthetic change', **extra):
        return approvals.request_action(self.user, {'operation': operation, 'payload': payload, 'summary': summary, **extra})

    def approve(self, item, user=None, **changes):
        return approvals.decide(user or self.user, item['id'], {'decision': 'approve', 'payload_hash': item['payload_hash']} | changes)

    def book(self):
        return ledger.create_book(self.user, self.entity.pk, PERIOD)

    def draft(self, book, reference='synthetic-receipt-1', amount='42.17'):
        accounts = {a['code']: a['id'] for a in book['accounts']}
        revision = ledger.read_book(self.user, book['id'])['revision']
        return ledger.propose_journal(self.user, book['id'], {
            'operation_id': str(uuid.uuid4()), 'expected_revision': revision, 'date': '2026-03-05',
            'description': 'Synthetic office purchase', 'source': {'kind': 'manual', 'reference': reference},
            'evidence': [reference], 'lines': [{'account_id': accounts['6200'], 'debit': amount, 'credit': '0'},
                                               {'account_id': accounts['1000'], 'debit': '0', 'credit': amount}]})

    def test_request_changes_nothing_and_approval_executes_exactly_the_requested_books(self):
        item = self.request('books.create', {'entity_id': str(self.entity.pk), **PERIOD}, 'Create USD books for 2026')
        self.assertEqual((item['state'], item['operation'], item['summary']), ('pending', 'books.create', 'Create USD books for 2026'))
        self.assertRegex(item['payload_hash'], '^[a-f0-9]{64}$')
        self.assertEqual(Book.objects.count(), 0)
        self.assertEqual([r['id'] for r in approvals.list_requests(self.user)['pending']], [item['id']])
        done = self.approve(item)
        self.assertEqual((done['state'], done['error']), ('approved', None))
        saved = Book.objects.get()
        self.assertEqual((str(saved.entity_id), saved.currency, saved.created_by_id), (str(self.entity.pk), 'USD', self.user.pk))
        self.assertEqual(done['result']['id'], str(saved.pk))
        self.assertEqual(approvals.list_requests(self.user)['pending'], [])

    def test_repeated_approval_returns_the_original_result_without_executing_again(self):
        item = self.request('books.create', {'entity_id': str(self.entity.pk), **PERIOD})
        first = self.approve(item)
        again = self.approve(item)
        self.assertEqual(first, again)
        self.assertEqual(Book.objects.count(), 1)

    def test_rejection_changes_nothing_and_cannot_be_approved_afterwards(self):
        item = self.request('books.create', {'entity_id': str(self.entity.pk), **PERIOD})
        rejected = approvals.decide(self.user, item['id'], {'decision': 'reject', 'payload_hash': item['payload_hash']})
        self.assertEqual(rejected['state'], 'rejected')
        with self.assertRaises(approvals.ApprovalError):
            self.approve(item)
        self.assertEqual(Book.objects.count(), 0)

    def test_wrong_hash_foreign_user_viewer_and_extra_fields_cannot_decide(self):
        item = self.request('books.create', {'entity_id': str(self.entity.pk), **PERIOD})
        for user, changes in ((self.user, {'payload_hash': 'a' * 64}), (self.other, {}), (self.viewer, {}),
                              (self.user, {'actor_id': str(self.other.pk)}), (self.user, {'decision': 'execute'})):
            with self.assertRaises(approvals.ApprovalError):
                self.approve(item, user=user, **changes)
        self.assertEqual((Book.objects.count(), ActionRequest.objects.get().state), (0, 'pending'))
        self.assertEqual(approvals.list_requests(self.other)['pending'], [])

    def test_unknown_operations_malformed_payloads_and_foreign_targets_are_not_recorded(self):
        foreign = Entity.objects.create(engagement=Engagement.objects.create(
            owner=self.other, installation=Installation.objects.get(), name='Foreign'), name='Foreign business')
        for operation, payload in (('books.delete', {}), ('books.create', {'entity_id': str(self.entity.pk)}),
                                   ('books.create', {'entity_id': str(foreign.pk), **PERIOD}),
                                   ('books.create', {'entity_id': str(self.entity.pk), **PERIOD, 'created_by': '1'}),
                                   ('journal.post', {'book_id': str(uuid.uuid4()), 'journal_id': str(uuid.uuid4()), 'payload_hash': 'b' * 64})):
            with self.assertRaises(approvals.ApprovalError):
                self.request(operation, payload)
        with self.assertRaises(approvals.ApprovalError):
            approvals.request_action(self.viewer, {'operation': 'books.create', 'payload': {'entity_id': str(self.entity.pk), **PERIOD}, 'summary': 'x'})
        self.assertEqual(ActionRequest.objects.count(), 0)

    def test_approved_post_posts_the_exact_entry_and_a_changed_target_fails_without_posting(self):
        book = self.book()
        entry = self.draft(book)
        item = self.request('journal.post', {'book_id': book['id'], 'journal_id': entry['id'], 'payload_hash': entry['payload_hash']},
                            'Post office purchase 42.17')
        self.assertEqual(Journal.objects.get().state, 'draft')
        self.assertEqual(item['detail']['lines'], [{'account': '6200 Office expenses', 'debit': '42.1700', 'credit': '0.0000'},
                                                  {'account': '1000 Checking', 'debit': '0.0000', 'credit': '42.1700'}])
        self.assertEqual((item['detail']['date'], item['detail']['current']), ('2026-03-05', True))
        done = self.approve(item)
        self.assertEqual((done['state'], done['result']['state'], Journal.objects.get().state), ('approved', 'posted', 'posted'))
        report = ledger.journal_preview(self.user, book['id'])
        self.assertEqual((report['trial_debit'], report['trial_credit']), ('42.1700', '42.1700'))
        other = self.draft(book, 'synthetic-receipt-2', '5')
        stale = self.request('journal.post', {'book_id': book['id'], 'journal_id': other['id'], 'payload_hash': other['payload_hash']})
        Journal.objects.filter(pk=other['id']).update(payload_hash='c' * 64)
        failed = self.approve(stale)
        self.assertEqual((failed['state'], failed['result']), ('failed', None))
        self.assertTrue(failed['error'])
        self.assertEqual(Journal.objects.get(pk=other['id']).state, 'draft')

    def test_approved_statement_import_and_account_creation_use_current_book_state(self):
        book = self.book()
        imported = self.request('statement.import', {'book_id': book['id'], 'filename': 'synthetic-march.csv', 'content': CSV},
                                'Import 2 rows from synthetic-march.csv')
        account = self.request('account.create', {'book_id': book['id'], 'code': '6300', 'name': 'Rent', 'category': 'expense'})
        self.assertEqual((SourceRow.objects.count(), imported['payload']['content_bytes']), (0, len(CSV.encode())))
        self.assertNotIn('content', imported['payload'])
        self.assertEqual(self.approve(account)['state'], 'approved')
        done = self.approve(imported)
        self.assertEqual((done['state'], done['result']['row_count'], SourceRow.objects.count()), ('approved', 2, 2))
        self.assertIn('6300', {a['code'] for a in ledger.read_book(self.user, book['id'])['accounts']})

    def test_batch_decision_executes_each_request_and_reports_individual_outcomes(self):
        book = self.book()
        batch = str(uuid.uuid4())
        first, second = self.draft(book, 'synthetic-a', '10'), self.draft(book, 'synthetic-b', '20')
        items = [self.request('journal.post', {'book_id': book['id'], 'journal_id': e['id'], 'payload_hash': e['payload_hash']},
                              batch_id=batch) for e in (first, second)]
        Journal.objects.filter(pk=second['id']).update(payload_hash='d' * 64)
        outcome = approvals.decide_batch(self.user, batch, {'decision': 'approve',
                                                           'payload_hashes': {i['id']: i['payload_hash'] for i in items}})
        self.assertEqual([r['state'] for r in outcome['requests']], ['approved', 'failed'])
        self.assertEqual({j.state for j in Journal.objects.all()}, {'posted', 'draft'})
        with self.assertRaises(approvals.ApprovalError):
            approvals.decide_batch(self.other, batch, {'decision': 'approve', 'payload_hashes': {}})

    def test_http_lists_and_decides_only_for_the_bound_owner_and_never_from_get(self):
        item = self.request('books.create', {'entity_id': str(self.entity.pk), **PERIOD})
        header = {'HTTP_X_MAIPA_AGENT_ACTOR': str(self.user.pk)}
        self.client.force_login(self.user)
        listed = self.client.get('/api/approvals/', secure=True, **header)
        self.assertEqual([r['id'] for r in listed.json()['pending']], [item['id']])
        url = f"/api/approvals/{item['id']}/decide/"
        body = json.dumps({'decision': 'approve', 'payload_hash': item['payload_hash']})
        self.assertEqual(self.client.get(url, secure=True, **header).status_code, 405)
        self.assertEqual(self.client.post(url, data=body, content_type='application/json', secure=True).status_code, 409)
        done = self.client.post(url, data=body, content_type='application/json', secure=True, **header)
        self.assertEqual((done.status_code, done.json()['state'], Book.objects.count()), (200, 'approved', 1))
        self.client.force_login(self.other)
        self.assertEqual(self.client.get('/api/approvals/', secure=True, HTTP_X_MAIPA_AGENT_ACTOR=str(self.other.pk)).json()['pending'], [])


class AgentApprovalToolTests(TestCase):
    def setUp(self):
        from pathlib import Path
        import tempfile
        from django.test import override_settings
        from backend.maipa.agent_accounts import ui
        from backend.maipa.agent_accounts.models import AgentAccount
        from backend.maipa.agent_accounts.references import record_session
        from backend.maipa.storage import prepare_directory, prepare_files
        temporary = tempfile.TemporaryDirectory(prefix='maipa-approval-tools-')
        self.addCleanup(temporary.cleanup)
        data = Path(temporary.name).resolve() / 'install'
        prepare_directory(data); prepare_files(data)
        configured = override_settings(DATA_ROOT=data); configured.enable(); self.addCleanup(configured.disable)
        install = Installation.objects.create()
        self.user = get_user_model().objects.create_user(username='approval-tool-owner')
        Membership.objects.create(user=self.user, installation=install, role='preparer')
        self.entity = Entity.objects.create(engagement=Engagement.objects.create(
            owner=self.user, installation=install, name='Synthetic tool approvals'), name='Synthetic tool business')
        self.account = AgentAccount.objects.create(owner=self.user, provider='claude', label='Synthetic approval account')
        self.ui = ui
        self.lease = ui.open_ui_lease(self.user, str(self.account.pk), record_session(self.user, str(self.account.pk), 'synthetic-approval-handle'))

    def call(self, name, arguments):
        return self.ui.dispatch_ui(self.user, str(self.account.pk), self.lease,
                                   {'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}})

    def test_agent_can_request_and_read_but_has_no_tool_to_decide(self):
        asked = self.call('maipa_action_request', {'operation': 'books.create', 'summary': 'Create USD books for 2026',
                                                   'payload': {'entity_id': str(self.entity.pk), **PERIOD}})
        self.assertTrue(asked['requested'])
        self.assertEqual((asked['request']['state'], Book.objects.count()), ('pending', 0))
        self.assertEqual([r['id'] for r in self.call('maipa_action_list', {})['pending']], [asked['request']['id']])
        refused = self.call('maipa_action_request', {'operation': 'books.delete', 'payload': {}, 'summary': 'x'})
        self.assertEqual((refused['requested'], refused['reason']), (False, 'approval_operation_unsupported'))
        names = {t['name'] for t in self.ui._tools()}
        self.assertTrue({'maipa_action_request', 'maipa_action_list'} <= names)
        self.assertFalse(any('decide' in n or 'approve' in n for n in names))
        for name in ('maipa_action_decide', 'maipa_action_approve'):
            with self.assertRaises(self.ui.UiUnavailable):
                self.call(name, {'id': asked['request']['id'], 'decision': 'approve', 'payload_hash': asked['request']['payload_hash']})
        self.assertEqual((Book.objects.count(), ActionRequest.objects.get().state), (0, 'pending'))
