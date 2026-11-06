"""Statement import: exact, atomic, idempotent source rows that journals must cite faithfully."""
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.maipa.ledger import services
from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.workspace.models import Installation, Membership

CSV = 'date,description,amount,reference\n2026-03-01,Synthetic client payment,1250.5,dep-1\n2026-03-02,Synthetic coffee beans,-0.1,\n2026-03-03,"Synthetic rent, March",-900,chk-1001\n'


class SourceImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='import-owner')
        cls.other = get_user_model().objects.create_user(username='import-other')
        cls.member = Membership.objects.create(user=cls.user, installation=install, role='preparer')
        Membership.objects.create(user=cls.other, installation=install, role='administrator')
        engagement = Engagement.objects.create(owner=cls.user, installation=install, name='Synthetic imports')
        cls.entity = Entity.objects.create(engagement=engagement, name='Synthetic import business')
        cls.sibling = Entity.objects.create(engagement=engagement, name='Synthetic sibling import business')

    def setUp(self):
        self.book = services.create_book(self.user, self.entity.pk,
            {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'})
        self.accounts = {a['code']: a['id'] for a in self.book['accounts']}

    def revision(self, book_id=None):
        return services.read_book(self.user, book_id or self.book['id'])['revision']

    def upload(self, content=CSV, name='synthetic-march.csv', user=None, book_id=None):
        return services.import_statement(user or self.user, book_id or self.book['id'],
            {'expected_revision': self.revision(book_id), 'filename': name, 'content': content})

    def propose_from(self, row, debit, credit, amount, day=None):
        return services.propose_journal(self.user, self.book['id'], {
            'operation_id': str(uuid.uuid4()), 'expected_revision': self.revision(), 'date': day or row['date'],
            'description': row['description'], 'source': {'kind': 'csv', 'reference': row['id']},
            'evidence': [f"{row['import_filename']} row {row['row_number']}"],
            'lines': [{'account_id': self.accounts[debit], 'debit': amount, 'credit': '0'},
                      {'account_id': self.accounts[credit], 'debit': '0', 'credit': amount}]})

    def test_statement_rows_are_saved_exactly_with_file_identity_and_listed_as_pending(self):
        result = self.upload()
        self.assertEqual((result['filename'], result['row_count'], result['created']), ('synthetic-march.csv', 3, True))
        self.assertRegex(result['sha256'], '^[a-f0-9]{64}$')
        rows = services.source_rows(self.user, self.book['id'])['rows']
        self.assertEqual([(r['row_number'], r['date'], r['description'], r['amount'], r['reference'], r['status']) for r in rows],
                         [(1, '2026-03-01', 'Synthetic client payment', '1250.5000', 'dep-1', 'pending'),
                          (2, '2026-03-02', 'Synthetic coffee beans', '-0.1000', '', 'pending'),
                          (3, '2026-03-03', 'Synthetic rent, March', '-900.0000', 'chk-1001', 'pending')])
        self.assertTrue(all(r['import_id'] == result['id'] for r in rows))
        self.assertEqual(services.read_book(self.user, self.book['id'])['journals'], [])

    def test_reimporting_the_same_file_returns_the_original_import_without_new_rows(self):
        first = self.upload()
        again = self.upload(name='renamed-copy.csv')
        self.assertEqual((again['id'], again['created'], again['filename']), (first['id'], False, 'synthetic-march.csv'))
        self.assertEqual(len(services.source_rows(self.user, self.book['id'])['rows']), 3)

    def test_one_bad_row_rejects_the_whole_file_with_its_row_number(self):
        before = self.revision()
        for content, row in (
                ('date,description,amount\n2026-03-01,Ok,1\n2026-02-30,Bad date,1\n', 2),
                ('date,description,amount\n2026-03-01,Ok,1\n2026-03-02,Bad amount,1.23456\n', 2),
                ('date,description,amount\n2026-03-01,Zero,0\n', 1),
                ('date,description,amount\n2027-01-01,Outside period,5\n', 1),
                ('date,description,amount\n2026-03-01,,5\n', 1)):
            with self.assertRaises(services.LedgerError) as caught:
                self.upload(content)
            self.assertEqual(caught.exception.code, f'import_row_{row}_invalid')
        for content in ('when,what,how much\n2026-03-01,x,1\n', '', 'date,description,amount\n',
                        'date,description,amount,extra,more\n2026-03-01,x,1,a,b\n'):
            with self.assertRaises(services.LedgerError):
                self.upload(content)
        self.assertEqual((self.revision(), services.source_rows(self.user, self.book['id'])['rows']), (before, []))

    def test_journal_citing_an_imported_row_must_match_its_date_and_amount_and_marks_it_proposed(self):
        self.upload()
        rows = {r['row_number']: r for r in services.source_rows(self.user, self.book['id'])['rows']}
        with self.assertRaises(services.LedgerError) as caught:
            self.propose_from(rows[3], '6000', '1000', '900.01')
        self.assertEqual(caught.exception.code, 'import_amount_mismatch')
        with self.assertRaises(services.LedgerError) as caught:
            self.propose_from(rows[3], '6000', '1000', '900', day='2026-03-04')
        self.assertEqual(caught.exception.code, 'import_date_mismatch')
        entry = self.propose_from(rows[3], '6000', '1000', '900')
        self.assertEqual(entry['source'], {'kind': 'csv', 'reference': rows[3]['id']})
        listed = {r['row_number']: r for r in services.source_rows(self.user, self.book['id'])['rows']}
        self.assertEqual((listed[3]['status'], listed[3]['journal_id']), ('proposed', entry['id']))
        self.assertEqual(listed[1]['status'], 'pending')
        with self.assertRaises(services.LedgerError) as caught:
            self.propose_from(rows[3], '6000', '1000', '900')
        self.assertEqual(caught.exception.code, 'ledger_source_exists')

    def test_unknown_foreign_or_sibling_book_rows_cannot_be_cited_or_imported_into(self):
        sibling = services.create_book(self.user, self.sibling.pk,
            {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'})
        self.upload(book_id=sibling['id'])
        sibling_row = services.source_rows(self.user, sibling['id'])['rows'][0]
        for reference in (sibling_row['id'], str(uuid.uuid4()), 'not-a-row'):
            with self.assertRaises(services.LedgerError):
                self.propose_from({**sibling_row, 'id': reference}, '1000', '4000', '1250.5')
        with self.assertRaises(services.LedgerError):
            services.import_statement(self.other, self.book['id'],
                {'expected_revision': self.revision(), 'filename': 'x.csv', 'content': CSV})
        with self.assertRaises(services.LedgerError):
            services.source_rows(self.other, self.book['id'])

    def test_size_row_and_field_limits_and_stale_revision_are_refused(self):
        many = 'date,description,amount\n' + ''.join(f'2026-03-01,Row {i},1\n' for i in range(1001))
        for value in ({'expected_revision': self.revision(), 'filename': 'x.csv', 'content': many},
                      {'expected_revision': self.revision(), 'filename': 'x.csv', 'content': 'a' * 524_289},
                      {'expected_revision': self.revision(), 'filename': '../x.csv', 'content': CSV},
                      {'expected_revision': self.revision() + 1, 'filename': 'x.csv', 'content': CSV},
                      {'expected_revision': self.revision(), 'filename': 'x.csv', 'content': CSV, 'actor_id': '1'}):
            with self.assertRaises(services.LedgerError):
                services.import_statement(self.user, self.book['id'], value)
        self.assertEqual(services.source_rows(self.user, self.book['id'])['rows'], [])

    def test_http_import_is_human_bound_idempotent_and_reads_back_rows(self):
        url = f"/api/books/{self.book['id']}/imports/"
        self.client.force_login(self.user)
        header = {'HTTP_X_MAIPA_AGENT_ACTOR': str(self.user.pk)}
        body = {'expected_revision': self.revision(), 'filename': 'synthetic-march.csv', 'content': CSV}
        self.assertEqual(self.client.post(url, data=body, content_type='application/json', secure=True).status_code, 409)
        created = self.client.post(url, data=body, content_type='application/json', secure=True, **header)
        self.assertEqual((created.status_code, created.json()['row_count']), (201, 3))
        again = self.client.post(url, data=body, content_type='application/json', secure=True, **header)
        self.assertEqual((again.status_code, again.json()['id'], again.json()['created']), (200, created.json()['id'], False))
        listed = self.client.get(url, secure=True, **header)
        self.assertEqual(listed.json(), services.source_rows(self.user, self.book['id']))
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url, secure=True, HTTP_X_MAIPA_AGENT_ACTOR=str(self.other.pk)).status_code, 404)
