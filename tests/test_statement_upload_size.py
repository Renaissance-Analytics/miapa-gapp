"""Statement uploads accept the documented 512 KB file size over HTTP; other endpoints keep the small request limit."""
import base64
import hashlib
import json

import holy_sheet
from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.maipa.approvals.models import ChatAttachment
from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.ledger import services as ledger
from backend.maipa.ledger.models import SourceRow
from backend.maipa.workspace.models import Installation, Membership

PERIOD = {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'}


def noise(number, length):
    """Text that does not compress away, so file sizes are honest."""
    text = ''
    while len(text) < length:
        text += hashlib.sha256(f'{number}:{len(text)}'.encode()).hexdigest()
    return text[:length]


def statement(rows, width):
    return 'date,description,amount\n' + ''.join(f'2026-03-01,{noise(n, width)},{n + 1}\n' for n in range(rows))


class StatementUploadSizeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='size-owner')
        Membership.objects.create(user=cls.user, installation=install, role='preparer')
        cls.entity = Entity.objects.create(engagement=Engagement.objects.create(
            owner=cls.user, installation=install, name='Synthetic sizes'), name='Synthetic size business')

    def setUp(self):
        self.client.force_login(self.user)
        self.actor = {'HTTP_X_MAIPA_AGENT_ACTOR': str(self.user.pk)}

    def post(self, url, value):
        return self.client.post(url, data=json.dumps(value), content_type='application/json', secure=True, **self.actor)

    def test_a_csv_attachment_near_the_documented_limit_is_stored_and_one_over_it_is_refused(self):
        content = statement(1000, 480)
        self.assertTrue(450_000 < len(content.encode()) <= 524_288)
        made = self.post('/api/agent/attachments/', {'name': 'large.csv', 'content': content})
        self.assertEqual((made.status_code, made.json()['size'], made.json()['lines']), (201, len(content.encode()), 1001))
        over = self.post('/api/agent/attachments/', {'name': 'over.csv', 'content': 'x' * 524_289})
        self.assertEqual((over.status_code, over.json()['error']['code']), (400, 'attachment_too_large'))
        huge = self.post('/api/agent/attachments/', {'name': 'huge.csv', 'content': 'x' * 1_100_000})
        self.assertEqual((huge.status_code, huge.json()['error']['code']), (413, 'request_too_large'))
        self.assertEqual(ChatAttachment.objects.count(), 1)

    def test_a_workbook_attachment_of_several_hundred_kilobytes_is_stored(self):
        rows = [['2026-03-01', noise(n, 400), n + 1] for n in range(1000)]
        data = holy_sheet.to_bytes(holy_sheet.from_array(rows, headers=['Date', 'Description', 'Amount'], sheet_name='Statement'))
        self.assertTrue(150_000 < len(data) <= 524_288, len(data))
        made = self.post('/api/agent/attachments/', {'name': 'large.xlsx', 'content_base64': base64.b64encode(data).decode('ascii')})
        self.assertEqual((made.status_code, made.json()['size'], made.json()['lines']), (201, len(data), 1001))
        self.assertEqual(bytes(ChatAttachment.objects.get().original), data)

    def test_a_statement_near_the_documented_limit_imports_through_the_books_endpoint(self):
        book = ledger.create_book(self.user, self.entity.pk, PERIOD)
        content = statement(1000, 480)
        made = self.post(f"/api/books/{book['id']}/imports/", {'expected_revision': 0, 'filename': 'large.csv', 'content': content})
        self.assertEqual((made.status_code, made.json()['row_count'], SourceRow.objects.count()), (201, 1000, 1000))
        huge = self.post(f"/api/books/{book['id']}/imports/", {'expected_revision': 1, 'filename': 'huge.csv', 'content': 'x' * 1_100_000})
        self.assertEqual((huge.status_code, huge.json()['error']['code']), (413, 'request_too_large'))

    def test_other_endpoints_keep_the_small_request_limit(self):
        book = ledger.create_book(self.user, self.entity.pk, PERIOD)
        padded = {'expected_revision': 0, 'code': '6300', 'name': 'Rent', 'category': 'expense', 'note': 'x' * 70_000}
        self.assertEqual(self.post(f"/api/books/{book['id']}/accounts/", padded).status_code, 413)
        self.assertEqual(self.post('/api/approvals/00000000-0000-4000-8000-000000000000/decide/',
                                   {'decision': 'approve', 'payload_hash': 'x' * 70_000}).status_code, 413)

    def test_a_large_attached_statement_is_imported_whole_by_an_approved_request(self):
        from backend.maipa.approvals import attachments, services as approvals
        book = ledger.create_book(self.user, self.entity.pk, PERIOD)
        content = statement(1000, 480)
        saved = self.post('/api/agent/attachments/', {'name': 'large.csv', 'content': content}).json()
        item = approvals.request_action(self.user, {'operation': 'statement.import', 'summary': 'Import the large statement',
            'payload': attachments.resolve_payload(self.user, 'statement.import', {'book_id': book['id'], 'attachment_id': saved['id']})})
        self.assertEqual((item['payload']['content_bytes'], item['payload']['content_sha256']), (len(content.encode()), saved['sha256']))
        done = approvals.decide(self.user, item['id'], {'decision': 'approve', 'payload_hash': item['payload_hash']})
        self.assertEqual((done['state'], done['result']['row_count'], SourceRow.objects.count()), ('approved', 1000, 1000))
