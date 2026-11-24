"""Moving an install between databases: every record arrives exactly, or nothing does."""
import io
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TransactionTestCase

from backend.maipa import datacopy
from backend.maipa.approvals import services as approvals
from backend.maipa.approvals.models import ActionRequest
from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.ledger import services as ledger
from backend.maipa.ledger.models import Journal, JournalLine, LedgerEvent
from backend.maipa.workspace.models import BootstrapState, Installation, Membership, PersonalViews

MARCH = 'date,description,amount\n2026-03-05,Synthetic rent,-900\n2026-03-20,Bank fee,-25.5\n'
NOTE = {'id': 'review-note', 'type': 'Maipa.Note', 'props': {'text': 'Synthetic note with ünïcode and "quotes"'}}


class DatabaseCopyTests(TransactionTestCase):
    databases = {'default', 'copy_target'}
    serialized_rollback = True

    def setUp(self):
        BootstrapState.objects.update_or_create(slot=1, defaults={'closed': True})
        install = Installation.objects.create()
        self.user = get_user_model().objects.create_user(username='copy-owner', password='synthetic-password-for-copy')
        self.other = get_user_model().objects.create_user(username='copy-other')
        Membership.objects.create(user=self.user, installation=install, role='administrator')
        Membership.objects.create(user=self.other, installation=install, role='preparer')
        entity = Entity.objects.create(engagement=Engagement.objects.create(owner=self.user, installation=install, name='Synthetic copy client'),
                                       name='Synthetic copy business')
        self.book = ledger.create_book(self.user, entity.pk, {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'})
        accounts = {a['code']: a['id'] for a in self.book['accounts']}
        ledger.import_statement(self.user, self.book['id'], {'expected_revision': 0, 'filename': 'march.csv', 'content': MARCH, 'account_id': accounts['1000']})
        row = next(r for r in ledger.source_rows(self.user, self.book['id'])['rows'] if r['description'] == 'Synthetic rent')
        made = ledger.propose_journal(self.user, self.book['id'], {
            'operation_id': str(uuid.uuid4()), 'expected_revision': 1, 'date': '2026-03-05', 'description': 'Synthetic rent',
            'source': {'kind': 'csv', 'reference': row['id']}, 'evidence': [row['id']],
            'lines': [{'account_id': accounts['6200'], 'debit': '900', 'credit': '0'}, {'account_id': accounts['1000'], 'debit': '0', 'credit': '900'}]})
        request = approvals.request_action(self.user, {'operation': 'journal.post', 'summary': 'Post the rent entry',
            'payload': {'book_id': self.book['id'], 'journal_id': made['id'], 'payload_hash': made['payload_hash']}})
        approvals.decide(self.user, request['id'], {'decision': 'approve', 'payload_hash': request['payload_hash']})
        approvals.request_action(self.user, {'operation': 'account.create', 'summary': 'Add rent account',
            'payload': {'book_id': self.book['id'], 'code': '6300', 'name': 'Synthetic rent', 'category': 'expense'}})
        PersonalViews.objects.create(user=self.user, installation=install, revision=1,
            definitions=[{'id': str(uuid.uuid4()), 'title': 'Review note', 'schema': NOTE, 'revision': 1, 'pinned': True, 'history': []}])

    def test_every_record_arrives_exactly_and_the_copy_keeps_working(self):
        before = datacopy.digest('default')
        counts = datacopy.copy('default', 'copy_target')
        self.assertEqual(datacopy.compare('default', 'copy_target'), {})
        self.assertEqual(datacopy.digest('default'), before)                      # The source is only read.
        self.assertEqual(counts, {label: value['rows'] for label, value in before.items()})
        self.assertEqual((counts['auth.User'], counts['ledger.Journal'], counts['ledger.JournalLine'], counts['approvals.ActionRequest'], counts['workspace.BootstrapState']),
                         (2, 1, 2, 2, 1))
        self.assertNotIn('auth.Permission', counts); self.assertNotIn('contenttypes.ContentType', counts)
        there = Journal.objects.using('copy_target').get()
        self.assertEqual((there.state, there.total_units, JournalLine.objects.using('copy_target').filter(journal=there).count(),
                          LedgerEvent.objects.using('copy_target').count()), ('posted', 9000000, 2, LedgerEvent.objects.count()))
        self.assertEqual(PersonalViews.objects.using('copy_target').get().definitions[0]['schema'], NOTE)
        self.assertEqual(sorted(ActionRequest.objects.using('copy_target').values_list('state', flat=True)), ['approved', 'pending'])
        copied = get_user_model().objects.using('copy_target').get(username='copy-owner')
        self.assertTrue(copied.check_password('synthetic-password-for-copy'))
        self.assertTrue(BootstrapState.objects.using('copy_target').get(slot=1).closed)
        # New rows after the copy get fresh ids instead of colliding with copied ones.
        added = get_user_model().objects.db_manager('copy_target').create_user(username='added-after-copy')
        self.assertGreater(added.pk, max(self.user.pk, self.other.pk))
        Membership.objects.using('copy_target').create(user=added, installation=Installation.objects.using('copy_target').get(), role='viewer')

    def test_a_target_that_already_holds_records_is_refused_and_left_alone(self):
        datacopy.copy('default', 'copy_target')
        held = datacopy.digest('copy_target')
        get_user_model().objects.create_user(username='arrived-later')
        with self.assertRaises(datacopy.CopyRefused) as refused:
            datacopy.copy('default', 'copy_target')
        self.assertEqual(refused.exception.code, 'target_not_empty')
        self.assertEqual(datacopy.digest('copy_target'), held)
        self.assertFalse(get_user_model().objects.using('copy_target').filter(username='arrived-later').exists())

    def test_any_difference_between_the_two_databases_is_reported_by_model(self):
        datacopy.copy('default', 'copy_target')
        Journal.objects.using('copy_target').update(description='Altered after the copy')
        JournalLine.objects.using('copy_target').filter(position=1)._raw_delete('copy_target')
        get_user_model().objects.using('copy_target').filter(username='copy-other').update(is_active=False)
        differences = datacopy.compare('default', 'copy_target')
        self.assertEqual(differences, {'ledger.Journal': {'source_rows': 1, 'target_rows': 1, 'same_content': False},
                                       'ledger.JournalLine': {'source_rows': 2, 'target_rows': 1, 'same_content': False},
                                       'auth.User': {'source_rows': 2, 'target_rows': 2, 'same_content': False}})

    def test_same_unknown_or_unsupported_sources_are_refused_before_anything_is_written(self):
        for source, target, code in (('default', 'default', 'same_database'), ('default', 'elsewhere', 'unknown_database'),
                                     ('elsewhere', 'copy_target', 'unknown_database'), (None, 'copy_target', 'unknown_database')):
            with self.assertRaises(datacopy.CopyRefused, msg=(source, target)) as refused:
                datacopy.copy(source, target)
            self.assertEqual(refused.exception.code, code)
        self.user.user_permissions.add(Permission.objects.first())
        with self.assertRaises(datacopy.CopyRefused) as refused:
            datacopy.copy('default', 'copy_target')
        self.assertEqual(refused.exception.code, 'unsupported_permission_rows')
        self.assertFalse(get_user_model().objects.using('copy_target').exists())

    def test_the_command_reports_counts_only_and_fails_loudly_on_refusal_or_difference(self):
        out = io.StringIO()
        call_command('copy_database', source='default', target='copy_target', stdout=out)
        text = out.getvalue()
        self.assertIn('auth.User: 2', text); self.assertIn('ledger.JournalLine: 2', text)
        self.assertIn('IDENTICAL: every model has the same rows in both databases', text)
        for private in ('copy-owner', 'Synthetic rent', 'synthetic-password-for-copy', 'pbkdf2'):
            self.assertNotIn(private, text)
        again = io.StringIO()
        call_command('copy_database', source='default', target='copy_target', verify_only=True, stdout=again)
        self.assertEqual(again.getvalue().strip(), 'IDENTICAL: every model has the same rows in both databases')
        with self.assertRaisesMessage(CommandError, 'refused: target_not_empty'):
            call_command('copy_database', source='default', target='copy_target', stdout=io.StringIO())
        Journal.objects.using('copy_target').update(description='Altered after the copy')
        changed = io.StringIO()
        with self.assertRaisesMessage(CommandError, 'databases differ in 1 models'):
            call_command('copy_database', source='default', target='copy_target', verify_only=True, stdout=changed)
        self.assertEqual(changed.getvalue().strip(), 'DIFFERENT ledger.Journal: source 1 rows, target 1 rows')
        with self.assertRaisesMessage(CommandError, 'refused: unknown_database'):
            call_command('copy_database', source='default', target='elsewhere', stdout=io.StringIO())
