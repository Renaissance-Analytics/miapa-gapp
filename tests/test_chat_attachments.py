"""Files the person attaches in chat reach the agent as owned, bounded text, and can be imported by approval."""
import json
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.maipa.approvals import attachments, services as approvals
from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.ledger import services as ledger
from backend.maipa.ledger.models import SourceRow
from backend.maipa.workspace.models import Installation, Membership

CSV = 'date,description,amount\n2026-03-01,Synthetic client payment,1250.5\n2026-03-03,Synthetic rent,-900\n'
PERIOD = {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'}


class ChatAttachmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='attachment-owner')
        cls.other = get_user_model().objects.create_user(username='attachment-other')
        cls.viewer = get_user_model().objects.create_user(username='attachment-viewer')
        Membership.objects.create(user=cls.user, installation=install, role='preparer')
        Membership.objects.create(user=cls.other, installation=install, role='administrator')
        Membership.objects.create(user=cls.viewer, installation=install, role='viewer')
        cls.entity = Entity.objects.create(engagement=Engagement.objects.create(
            owner=cls.user, installation=install, name='Synthetic attachments'), name='Synthetic attachment business')

    def test_stored_file_is_exact_owned_and_read_back_with_a_bounded_preview(self):
        saved = attachments.store(self.user, {'name': 'synthetic-march.csv', 'content': CSV})
        self.assertEqual((saved['name'], saved['size'], saved['lines']), ('synthetic-march.csv', len(CSV.encode()), 3))
        self.assertRegex(saved['sha256'], '^[a-f0-9]{64}$')
        self.assertNotIn('content', saved)
        read = attachments.read(self.user, saved['id'])
        self.assertEqual((read['preview'], read['truncated']), (CSV, False))
        self.assertEqual(attachments.content(self.user, saved['id']), ('synthetic-march.csv', CSV))
        big = attachments.store(self.user, {'name': 'big.txt', 'content': 'x' * 10000})
        self.assertEqual((len(attachments.read(self.user, big['id'])['preview']), attachments.read(self.user, big['id'])['truncated']), (4000, True))

    def test_foreign_unknown_viewer_and_invalid_files_are_refused(self):
        saved = attachments.store(self.user, {'name': 'synthetic-march.csv', 'content': CSV})
        for user, identity in ((self.other, saved['id']), (self.user, str(uuid.uuid4())), (self.user, 'not-an-id')):
            with self.assertRaises(attachments.AttachmentError):
                attachments.read(user, identity)
        for user, value in ((self.viewer, {'name': 'a.csv', 'content': CSV}), (self.user, {'name': '../a.csv', 'content': CSV}),
                            (self.user, {'name': 'a.csv', 'content': ''}), (self.user, {'name': 'a.csv', 'content': 'x' * 524_289}),
                            (self.user, {'name': 'a.csv', 'content': 'bin\x00ary'}), (self.user, {'name': 'a.csv', 'content': CSV, 'user': '1'})):
            with self.assertRaises(attachments.AttachmentError):
                attachments.store(user, value)
        self.assertEqual(len(attachments.listing(self.user)), 1)

    def test_statement_import_request_by_attachment_binds_the_exact_file_and_imports_on_approval(self):
        book = ledger.create_book(self.user, self.entity.pk, PERIOD)
        saved = attachments.store(self.user, {'name': 'synthetic-march.csv', 'content': CSV})
        item = approvals.request_action(self.user, {'operation': 'statement.import', 'summary': 'Import the attached statement',
            'payload': attachments.resolve_payload(self.user, 'statement.import', {'book_id': book['id'], 'attachment_id': saved['id']})})
        self.assertEqual((item['payload']['filename'], item['payload']['content_sha256']), ('synthetic-march.csv', saved['sha256']))
        self.assertEqual(SourceRow.objects.count(), 0)
        done = approvals.decide(self.user, item['id'], {'decision': 'approve', 'payload_hash': item['payload_hash']})
        self.assertEqual((done['state'], done['result']['row_count'], SourceRow.objects.count()), ('approved', 2, 2))
        with self.assertRaises(attachments.AttachmentError):
            attachments.resolve_payload(self.other, 'statement.import', {'book_id': book['id'], 'attachment_id': saved['id']})
        self.assertEqual(attachments.resolve_payload(self.user, 'books.create', {'entity_id': 'x'}), {'entity_id': 'x'})

    def test_http_upload_is_bound_to_the_owner_and_never_lists_foreign_files(self):
        self.client.force_login(self.user)
        header = {'HTTP_X_MAIPA_AGENT_ACTOR': str(self.user.pk)}
        body = json.dumps({'name': 'synthetic-march.csv', 'content': CSV})
        self.assertEqual(self.client.post('/api/agent/attachments/', data=body, content_type='application/json', secure=True).status_code, 409)
        made = self.client.post('/api/agent/attachments/', data=body, content_type='application/json', secure=True, **header)
        self.assertEqual((made.status_code, made.json()['lines']), (201, 3))
        self.assertNotIn('content', made.json())
        self.assertEqual(self.client.get('/api/agent/attachments/', secure=True, **header).status_code, 405)
        self.client.force_login(self.other)
        with self.assertRaises(attachments.AttachmentError):
            attachments.read(self.other, made.json()['id'])
