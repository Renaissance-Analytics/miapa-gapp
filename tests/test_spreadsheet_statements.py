"""An attached Excel statement becomes the exact same rows as its CSV, or is refused naming the problem."""
import base64
import hashlib
import json

import holy_sheet
from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.maipa.approvals import attachments, services as approvals
from backend.maipa.approvals.models import ChatAttachment
from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.ledger import services as ledger, spreadsheet
from backend.maipa.ledger.models import SourceRow
from backend.maipa.workspace.models import Installation, Membership

PERIOD = {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'}
HEADERS = ['Date', 'Description', 'Amount', 'Reference']
ROWS = [['2026-03-01', 'Synthetic client payment', 1250.5, 'dep-1'], ['2026-03-03', 'Synthetic rent, March', -900, 1001],
        ['2026-03-05', 'Synthetic "quoted" fee', 0.1, None]]
CSV = ('date,description,amount,reference\r\n2026-03-01,Synthetic client payment,1250.5,dep-1\r\n'
       '2026-03-03,"Synthetic rent, March",-900,1001\r\n2026-03-05,"Synthetic ""quoted"" fee",0.1,\r\n')


def workbook(rows=ROWS, headers=HEADERS, sheets=1):
    built = holy_sheet.from_array(rows, headers=headers, sheet_name='Statement')
    for extra in range(1, sheets):
        built['sheets'].append({**built['sheets'][0], 'name': f'Other {extra}'})
    return holy_sheet.to_bytes(built)


def encoded(data):
    return base64.b64encode(data).decode('ascii')


class SpreadsheetStatementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='sheet-owner')
        cls.viewer = get_user_model().objects.create_user(username='sheet-viewer')
        Membership.objects.create(user=cls.user, installation=install, role='preparer')
        Membership.objects.create(user=cls.viewer, installation=install, role='viewer')
        cls.entity = Entity.objects.create(engagement=Engagement.objects.create(
            owner=cls.user, installation=install, name='Synthetic sheets'), name='Synthetic sheet business')

    def test_a_statement_workbook_converts_to_exactly_the_equivalent_csv(self):
        self.assertEqual(spreadsheet.statement_csv(workbook()), CSV)
        three = spreadsheet.statement_csv(workbook([row[:3] for row in ROWS], HEADERS[:3]))
        self.assertEqual(three.splitlines()[:2], ['date,description,amount', '2026-03-01,Synthetic client payment,1250.5'])
        spaced = spreadsheet.statement_csv(workbook([['2026-03-01', '  Padded deposit ', '12.50', ''], [None, None, None, None], ['2026-03-02', 'After a blank row', 3, '']],
                                                    [' DATE ', 'description', 'Amount', 'reference']))
        self.assertEqual(spaced, 'date,description,amount,reference\r\n2026-03-01,Padded deposit,12.5,\r\n2026-03-02,After a blank row,3,\r\n')

    def test_unreadable_oversized_multi_sheet_and_malformed_workbooks_are_refused_naming_the_problem(self):
        cases = ((b'not a workbook', 'import_workbook_invalid'), (b'', 'import_workbook_invalid'), ('text', 'import_workbook_invalid'),
                 (workbook(sheets=2), 'import_workbook_sheets'),
                 (workbook(ROWS, ['When', 'Description', 'Amount', 'Reference']), 'import_header_invalid'),
                 (workbook([row + ['extra'] for row in ROWS], HEADERS + ['Balance']), 'import_header_invalid'),
                 (workbook([], HEADERS), 'import_row_count_invalid'),
                 (workbook([['2026-03-01', 'Deposit', 1, '']] * 1001, HEADERS), 'import_workbook_invalid'),
                 (workbook([['03/01/2026', 'Deposit', 1, '']], HEADERS), 'import_row_1_invalid'),
                 (workbook([ROWS[0], ['2026-03-03', 'Rent', 'nine hundred', '']], HEADERS), 'import_row_2_invalid'),
                 (workbook([ROWS[0], ['2026-03-03', 'Rent', 1.23456, '']], HEADERS), 'import_row_2_invalid'),
                 (workbook([ROWS[0], ROWS[1], ['2026-03-05', None, 5, '']], HEADERS), 'import_row_3_invalid'),
                 (workbook([['2026-03-01', 'Deposit', True, '']], HEADERS), 'import_row_1_invalid'))
        for data, code in cases:
            with self.assertRaises(ledger.LedgerError, msg=code) as refused:
                spreadsheet.statement_csv(data)
            self.assertEqual(refused.exception.code, code)

    def test_an_attached_workbook_is_kept_exactly_and_read_back_as_its_converted_rows(self):
        data = workbook()
        saved = attachments.store(self.user, {'name': 'synthetic-march.xlsx', 'content_base64': encoded(data)})
        self.assertEqual((saved['name'], saved['size'], saved['sha256'], saved['lines']),
                         ('synthetic-march.xlsx', len(data), hashlib.sha256(data).hexdigest(), 4))
        row = ChatAttachment.objects.get(pk=saved['id'])
        self.assertEqual((bytes(row.original), row.content), (data, CSV))
        read = attachments.read(self.user, saved['id'])
        self.assertEqual((read['preview'], read['converted_from'], read['truncated']), (CSV, 'xlsx', False))
        text = attachments.store(self.user, {'name': 'synthetic-march.csv', 'content': 'date,description,amount\n2026-03-01,Deposit,1\n'})
        self.assertEqual(attachments.read(self.user, text['id'])['converted_from'], None)
        self.assertIsNone(ChatAttachment.objects.get(pk=text['id']).original)

    def test_bad_names_encodings_sizes_and_viewers_cannot_store_a_workbook(self):
        good = encoded(workbook())
        for user, value in ((self.viewer, {'name': 'a.xlsx', 'content_base64': good}), (self.user, {'name': 'a.csv', 'content_base64': good}),
                            (self.user, {'name': 'a.xlsx', 'content_base64': 'not base64!'}), (self.user, {'name': 'a.xlsx', 'content_base64': ''}),
                            (self.user, {'name': 'a.xlsx', 'content_base64': encoded(b'x' * 524_289)}),
                            (self.user, {'name': 'a.xlsx', 'content_base64': encoded(b'not a workbook')}),
                            (self.user, {'name': 'a.xlsx', 'content_base64': good, 'content': 'x'}),
                            (self.user, {'name': 'a.xlsx', 'content_base64': 5}), (self.user, {'name': '../a.xlsx', 'content_base64': good})):
            with self.assertRaises(attachments.AttachmentError, msg=value['name']):
                attachments.store(user, value)
        with self.assertRaises(attachments.AttachmentError) as refused:
            attachments.store(self.user, {'name': 'a.xlsx', 'content_base64': encoded(workbook([['03/01/2026', 'Deposit', 1, '']]))})
        self.assertEqual(refused.exception.code, 'import_row_1_invalid')
        self.assertEqual(ChatAttachment.objects.count(), 0)

    def test_an_approved_import_of_an_attached_workbook_saves_the_same_rows_as_the_csv_would(self):
        book = ledger.create_book(self.user, self.entity.pk, PERIOD)
        saved = attachments.store(self.user, {'name': 'synthetic-march.xlsx', 'content_base64': encoded(workbook())})
        item = approvals.request_action(self.user, {'operation': 'statement.import', 'summary': 'Import the attached workbook',
            'payload': attachments.resolve_payload(self.user, 'statement.import', {'book_id': book['id'], 'attachment_id': saved['id']})})
        self.assertEqual((item['payload']['filename'], item['payload']['content_preview'], SourceRow.objects.count()), ('synthetic-march.xlsx', CSV, 0))
        done = approvals.decide(self.user, item['id'], {'decision': 'approve', 'payload_hash': item['payload_hash']})
        self.assertEqual((done['state'], done['result']['row_count']), ('approved', 3))
        self.assertEqual([(row['date'], row['description'], row['amount'], row['reference']) for row in ledger.source_rows(self.user, book['id'])['rows']],
                         [('2026-03-01', 'Synthetic client payment', '1250.5000', 'dep-1'), ('2026-03-03', 'Synthetic rent, March', '-900.0000', '1001'),
                          ('2026-03-05', 'Synthetic "quoted" fee', '0.1000', '')])

    def test_http_upload_accepts_a_workbook_for_the_bound_owner_and_reports_the_exact_refusal(self):
        self.client.force_login(self.user)
        actor = {'HTTP_X_MAIPA_AGENT_ACTOR': str(self.user.pk)}
        data = workbook()
        made = self.client.post('/api/agent/attachments/', data=json.dumps({'name': 'synthetic-march.xlsx', 'content_base64': encoded(data)}),
                                content_type='application/json', secure=True, **actor)
        self.assertEqual((made.status_code, made.json()['size'], made.json()['lines']), (201, len(data), 4))
        self.assertEqual(set(made.json()), {'id', 'name', 'size', 'sha256', 'lines', 'created_at'})
        bad = self.client.post('/api/agent/attachments/', data=json.dumps({'name': 'two.xlsx', 'content_base64': encoded(workbook(sheets=2))}),
                               content_type='application/json', secure=True, **actor)
        self.assertEqual((bad.status_code, bad.json()['error']['code']), (400, 'import_workbook_sheets'))
        self.assertEqual(ChatAttachment.objects.count(), 1)
