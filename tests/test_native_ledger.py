"""Independent native-book records and exact known answers, not posting approval."""
from copy import deepcopy
from datetime import date
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.maipa.ledger import services
from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.workspace.models import Installation, Membership


class NativeLedgerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.install = Installation.objects.create()
        cls.owner = get_user_model().objects.create_user(username='ledger-owner')
        cls.other = get_user_model().objects.create_user(username='ledger-other', is_staff=True, is_superuser=True)
        cls.member = Membership.objects.create(user=cls.owner, installation=cls.install, role='preparer')
        Membership.objects.create(user=cls.other, installation=cls.install, role='administrator')
        group = Engagement.objects.create(owner=cls.owner, installation=cls.install, name='Synthetic books')
        cls.entity = Entity.objects.create(engagement=group, name='Synthetic first business')
        cls.sibling = Entity.objects.create(engagement=group, name='Synthetic second business')

    def setup_body(self):
        return {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'}

    def book(self, entity=None):
        return services.create_book(self.owner, (entity or self.entity).pk, self.setup_body())

    def entry(self, book):
        accounts = {row['code']: row['id'] for row in book['accounts']}
        return {'operation_id': str(uuid.uuid4()), 'expected_revision': book['revision'],
                'date': '2026-11-04', 'description': 'Synthetic capital contribution',
                'source': {'kind': 'manual', 'reference': 'synthetic-source-1'},
                'evidence': ['synthetic-workpaper-1'],
                'lines': [{'account_id': accounts['1000'], 'debit': '0.10', 'credit': '0'},
                          {'account_id': accounts['1000'], 'debit': '0.20', 'credit': '0'},
                          {'account_id': accounts['3000'], 'debit': '0', 'credit': '0.30'}]}

    def rows(self):
        from backend.maipa.ledger.models import Book, Account, Journal, JournalLine, LedgerEvent
        return tuple(model.objects.count() for model in (Book, Account, Journal, JournalLine, LedgerEvent))

    def test_create_books_has_persisted_entity_currency_period_and_chart_without_transactions(self):
        book = self.book()
        reread = services.read_book(self.owner, book['id'])
        self.assertEqual(reread, book)
        self.assertEqual((book['entity_id'], book['currency'], book['period_start'], book['period_end']),
                         (str(self.entity.pk), 'USD', '2026-01-01', '2026-12-31'))
        self.assertEqual({row['category'] for row in book['accounts']},
                         {'asset', 'liability', 'equity', 'income', 'expense'})
        self.assertEqual((book['revision'], book['journals']), (0, []))
        self.assertEqual(self.rows()[2:4], (0, 0))

    def test_exact_decimal_lines_persist_as_draft_with_source_and_no_posted_balance(self):
        book = self.book()
        entry = services.propose_journal(self.owner, book['id'], self.entry(book))
        self.assertEqual((entry['state'], entry['debit'], entry['credit']), ('draft', '0.3000', '0.3000'))
        saved = services.read_book(self.owner, book['id'])
        self.assertEqual(saved['journals'], [entry])
        self.assertEqual(entry['evidence'], ['synthetic-workpaper-1'])
        self.assertEqual(entry['source'], {'kind': 'manual', 'reference': 'synthetic-source-1'})
        preview = services.journal_preview(self.owner, book['id'])
        balances = {row['code']: row for row in preview['accounts']}
        self.assertEqual((balances['1000']['draft_debit'], balances['3000']['draft_credit']), ('0.3000', '0.3000'))
        self.assertEqual((preview['posted_debit'], preview['posted_credit'], preview['pending_count']), ('0.0000', '0.0000', 1))
        self.assertEqual(self.rows()[2:], (1, 3, 1))

    def test_repeated_exact_request_returns_same_record_without_another_event_or_revision(self):
        book = self.book(); value = self.entry(book)
        first = services.propose_journal(self.owner, book['id'], value)
        again = services.propose_journal(self.owner, book['id'], deepcopy(value))
        self.assertEqual(first, again)
        self.assertEqual((services.read_book(self.owner, book['id'])['revision'], self.rows()[2:]), (1, (1, 3, 1)))

    def test_reused_operation_with_different_payload_and_duplicate_source_are_refused(self):
        book = self.book(); value = self.entry(book)
        services.propose_journal(self.owner, book['id'], value)
        changed = deepcopy(value); changed['description'] = 'Different economic event'
        duplicate = deepcopy(value); duplicate.update(operation_id=str(uuid.uuid4()), expected_revision=1)
        for candidate in (changed, duplicate):
            with self.subTest(candidate=candidate), self.assertRaises(services.LedgerError):
                services.propose_journal(self.owner, book['id'], candidate)
        self.assertEqual(self.rows()[2:], (1, 3, 1))

    def test_invalid_amounts_and_line_sides_cannot_create_partial_journals(self):
        book = self.book(); original = self.entry(book)
        values = ['NaN', 'Infinity', '1e2', '-0.1', '0.00001', 0.10, True, ' 0.10', '00.10', '9'*100]
        for invalid in values:
            value = deepcopy(original); value['lines'][0]['debit'] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(services.LedgerError):
                services.propose_journal(self.owner, book['id'], value)
        for debit, credit in [('0', '0'), ('0.10', '0.10')]:
            value = deepcopy(original); value['lines'][0].update(debit=debit, credit=credit)
            with self.assertRaises(services.LedgerError):
                services.propose_journal(self.owner, book['id'], value)
        self.assertEqual((self.rows()[2:], services.read_book(self.owner, book['id'])['revision']), ((0, 0, 0), 0))

    def test_unbalanced_and_excess_total_journals_are_atomic_refusals(self):
        book = self.book(); original = self.entry(book)
        value = deepcopy(original); value['lines'][-1]['credit'] = '0.31'
        with self.assertRaises(services.LedgerError):
            services.propose_journal(self.owner, book['id'], value)
        value = deepcopy(original)
        value['lines'][0]['debit'] = '99999999999999.9999'
        value['lines'][1]['debit'] = '99999999999999.9999'
        value['lines'][-1]['credit'] = '99999999999999.9999'
        with self.assertRaises(services.LedgerError):
            services.propose_journal(self.owner, book['id'], value)
        self.assertEqual(self.rows()[2:], (0, 0, 0))

    def test_sibling_entity_accounts_cannot_be_used_even_by_same_owner(self):
        first = self.book(); second = self.book(self.sibling)
        value = self.entry(first); value['lines'][0]['account_id'] = second['accounts'][0]['id']
        with self.assertRaises(services.LedgerError):
            services.propose_journal(self.owner, first['id'], value)
        self.assertEqual(self.rows()[2:], (0, 0, 0))

    def test_foreign_administrator_cannot_read_or_write_another_owners_books(self):
        book = self.book(); value = self.entry(book)
        for operation in (lambda: services.read_book(self.other, book['id']),
                          lambda: services.propose_journal(self.other, book['id'], value),
                          lambda: services.create_book(self.other, self.entity.pk, self.setup_body())):
            with self.assertRaises(services.LedgerError):
                operation()
        self.assertEqual(self.rows()[2:], (0, 0, 0))

    def test_current_membership_role_and_user_enablement_are_required_for_every_mutation(self):
        book = self.book(); value = self.entry(book)
        for update in ({'role': 'viewer'}, {'role': 'reviewer'}, {'enabled': False}):
            Membership.objects.filter(pk=self.member.pk).update(**update)
            with self.assertRaises(services.LedgerError):
                services.propose_journal(self.owner, book['id'], value)
            Membership.objects.filter(pk=self.member.pk).update(role='preparer', enabled=True)
        get_user_model().objects.filter(pk=self.owner.pk).update(is_active=False)
        with self.assertRaises(services.LedgerError):
            services.propose_journal(self.owner, book['id'], value)
        self.assertEqual(self.rows()[2:], (0, 0, 0))

    def test_stale_revision_and_actor_or_currency_hints_cannot_mutate_books(self):
        book = self.book(); value = self.entry(book)
        account = services.create_account(self.owner, book['id'], {'expected_revision': 0,
            'code': '6300', 'name': 'Synthetic subscriptions', 'category': 'expense'})
        self.assertEqual((account['code'], account['name'], account['category']), ('6300', 'Synthetic subscriptions', 'expense'))
        with self.assertRaises(services.LedgerError):
            services.propose_journal(self.owner, book['id'], value)
        for key, hint in [('actor_id', self.owner.pk), ('currency', 'EUR'), ('state', 'posted')]:
            altered = deepcopy(value); altered['expected_revision'] = 1; altered[key] = hint
            with self.assertRaises(services.LedgerError):
                services.propose_journal(self.owner, book['id'], altered)
        self.assertEqual(self.rows()[2:], (0, 0, 1))

    def test_entry_date_must_be_real_canonical_and_in_book_period(self):
        book = self.book(); original = self.entry(book)
        for day in ['2026-02-30', '2025-12-31', '2027-01-01', '2026-1-1', date(2026, 1, 1)]:
            value = deepcopy(original); value['date'] = day
            with self.subTest(day=day), self.assertRaises(services.LedgerError):
                services.propose_journal(self.owner, book['id'], value)
        self.assertEqual(self.rows()[2:], (0, 0, 0))

    def test_existing_book_currency_and_period_cannot_be_silently_reinitialized(self):
        original = self.book()
        self.assertEqual(self.book(), original)
        for changed in ({'currency': 'EUR'}, {'period_end': '2027-12-31'}, {'period_start': '2027-01-01'}):
            with self.assertRaises(services.LedgerError):
                services.create_book(self.owner, self.entity.pk, self.setup_body() | changed)
        self.assertEqual(services.read_book(self.owner, original['id']), original)

    def test_duplicate_account_code_and_invalid_category_do_not_advance_revision(self):
        book = self.book()
        for code, category in [('1000', 'asset'), ('6400', 'unknown'), ('', 'expense')]:
            with self.assertRaises(services.LedgerError):
                services.create_account(self.owner, book['id'], {'expected_revision': 0,
                    'code': code, 'name': 'Synthetic account', 'category': category})
        self.assertEqual((services.read_book(self.owner, book['id'])['revision'], self.rows()[-1]), (0, 0))

    def test_saved_journal_lines_and_source_history_cannot_be_changed_or_deleted(self):
        book = self.book(); entry = services.propose_journal(self.owner, book['id'], self.entry(book))
        from backend.maipa.ledger.models import Journal, JournalLine, LedgerEvent
        for row in (Journal.objects.get(pk=entry['id']), JournalLine.objects.first(), LedgerEvent.objects.first()):
            with self.assertRaises(ValueError):
                row.save()
            with self.assertRaises(ValueError):
                row.delete()
        self.assertEqual(services.read_book(self.owner, book['id'])['journals'], [entry])
