"""Entity groups: several books of one owner combined into one set of statements, each entity shown beside the total."""
import json
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.maipa.approvals import services as approvals
from backend.maipa.approvals.models import ActionRequest
from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.ledger import groups, services as ledger
from backend.maipa.ledger.models import EntityGroup, Journal
from backend.maipa.workspace.models import Installation, Membership
from tests.test_bookkeeping_posting import BookkeepingPostingTests

PERIOD = {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'}


class EntityGroupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='group-owner')
        cls.other = get_user_model().objects.create_user(username='group-other')
        Membership.objects.create(user=cls.user, installation=install, role='preparer')
        Membership.objects.create(user=cls.other, installation=install, role='administrator')
        engagement = Engagement.objects.create(owner=cls.user, installation=install, name='Synthetic group client')
        cls.entities = [Entity.objects.create(engagement=engagement, name=name) for name in ('Synthetic Holding', 'Synthetic Shop', 'Synthetic Euro')]
        cls.foreign = Entity.objects.create(engagement=Engagement.objects.create(owner=cls.other, installation=install, name='Other client'), name='Other business')

    def setUp(self):
        self.holding = ledger.create_book(self.user, self.entities[0].pk, PERIOD)
        self.shop = ledger.create_book(self.user, self.entities[1].pk, PERIOD)
        self.euro = ledger.create_book(self.user, self.entities[2].pk, {**PERIOD, 'currency': 'EUR'})
        self.theirs = ledger.create_book(self.other, self.foreign.pk, PERIOD)
        self.entry(self.holding, '2026-02-10', 'Consulting income', (('1000', '5000', '0'), ('4000', '0', '5000')))
        self.entry(self.holding, '2026-03-05', 'Rent', (('6200', '900', '0'), ('1000', '0', '900')))
        self.entry(self.shop, '2026-03-12', 'Shop sales', (('1000', '1250.50', '0'), ('4000', '0', '1250.50')))
        self.entry(self.shop, '2026-03-20', 'Supplier bill', (('6200', '300', '0'), ('2000', '0', '300')))
        self.entry(self.shop, '2026-04-02', 'April sales', (('1000', '10', '0'), ('4000', '0', '10')))

    def entry(self, book, date, description, lines):
        accounts = {a['code']: a['id'] for a in ledger.read_book(self.user, book['id'])['accounts']}
        revision = lambda: ledger.read_book(self.user, book['id'])['revision']
        made = ledger.propose_journal(self.user, book['id'], {
            'operation_id': str(uuid.uuid4()), 'expected_revision': revision(), 'date': date, 'description': description,
            'source': {'kind': 'manual', 'reference': f'synthetic-{uuid.uuid4()}'}, 'evidence': ['synthetic'],
            'lines': [{'account_id': accounts[code], 'debit': debit, 'credit': credit} for code, debit, credit in lines]})
        ledger.confirm_own_journal(self.user, book['id'], made['id'], {'expected_revision': revision(), 'payload_hash': made['payload_hash'], 'confirm': True})

    def ask(self, user=None, **changes):
        payload = {'name': 'Synthetic family of companies', 'book_ids': [self.holding['id'], self.shop['id']], **changes}
        return approvals.request_action(user or self.user, {'operation': 'group.create', 'summary': 'Group the holding company and the shop', 'payload': payload})

    def approve(self, item):
        return approvals.decide(self.user, item['id'], {'decision': 'approve', 'payload_hash': item['payload_hash']})

    def grouped(self):
        done = self.approve(self.ask())
        self.assertEqual(done['state'], 'approved', done)
        return done['result']['group_id']

    def combined(self, group, start='2026-01-01', end='2026-03-31', user=None):
        return groups.combined_report(user or self.user, group, {'from': start, 'to': end})

    def test_an_approved_group_combines_its_books_with_each_entity_beside_the_total(self):
        item = self.ask()
        self.assertEqual(item['detail'], {'name': 'Synthetic family of companies', 'currency': 'USD', 'books': ['Synthetic Holding', 'Synthetic Shop'], 'current': True})
        self.assertEqual(EntityGroup.objects.count(), 0)
        journals = Journal.objects.count()
        done = self.approve(item)
        self.assertEqual(done['state'], 'approved')
        group = done['result']['group_id']
        self.assertEqual(groups.list_groups(self.user), {'groups': [{'id': group, 'name': 'Synthetic family of companies', 'currency': 'USD',
            'books': [{'book_id': self.holding['id'], 'name': 'Synthetic Holding'}, {'book_id': self.shop['id'], 'name': 'Synthetic Shop'}], 'eliminated_accounts': 0}]})
        report = self.combined(group)
        self.assertEqual((report['group_id'], report['name'], report['currency'], report['from'], report['to'], report['eliminated']),
                         (group, 'Synthetic family of companies', 'USD', '2026-01-01', '2026-03-31', False))
        self.assertEqual([(b['book_id'], b['name']) for b in report['books']], [(self.holding['id'], 'Synthetic Holding'), (self.shop['id'], 'Synthetic Shop')])
        income, sheet = report['income_statement'], report['balance_sheet']
        self.assertEqual(income['income'], [{'code': '4000', 'name': 'Sales', 'amounts': ['5000.0000', '1250.5000'], 'total': '6250.5000'}])
        self.assertEqual(income['expenses'], [{'code': '6200', 'name': 'Office expenses', 'amounts': ['900.0000', '300.0000'], 'total': '1200.0000'}])
        self.assertEqual((income['total_income'], income['total_expenses'], income['net_income']),
                         ({'amounts': ['5000.0000', '1250.5000'], 'total': '6250.5000'}, {'amounts': ['900.0000', '300.0000'], 'total': '1200.0000'},
                          {'amounts': ['4100.0000', '950.5000'], 'total': '5050.5000'}))
        self.assertEqual(sheet['assets'], [{'code': '1000', 'name': 'Checking', 'amounts': ['4100.0000', '1250.5000'], 'total': '5350.5000'}])
        self.assertEqual(sheet['liabilities'], [{'code': '2000', 'name': 'Accounts payable', 'amounts': ['0.0000', '300.0000'], 'total': '300.0000'}])
        self.assertEqual((sheet['as_of'], sheet['total_assets']['total'], sheet['total_liabilities']['total'], sheet['earnings_to_date']['total'],
                          sheet['total_liabilities_and_equity']), ('2026-03-31', '5350.5000', '300.0000', '5050.5000', {'amounts': ['4100.0000', '1250.5000'], 'total': '5350.5000'}))
        self.assertEqual(report['cash'], {'opening_cash': {'amounts': ['0.0000', '0.0000'], 'total': '0.0000'},
                                          'closing_cash': {'amounts': ['4100.0000', '1250.5000'], 'total': '5350.5000'},
                                          'net_change': {'amounts': ['4100.0000', '1250.5000'], 'total': '5350.5000'}})
        # Each column is exactly that entity's own report, and April is outside the range.
        for position, book in enumerate((self.holding, self.shop)):
            own = ledger.financial_report(self.user, book['id'], {'from': '2026-01-01', 'to': '2026-03-31'})
            self.assertEqual(income['net_income']['amounts'][position], own['income_statement']['net_income'])
            self.assertEqual(sheet['total_assets']['amounts'][position], own['balance_sheet']['total_assets'])
        self.assertEqual(self.combined(group, end='2026-04-30')['income_statement']['total_income']['total'], '6260.5000')
        # Grouping and reading change no entry and no book; approving again changes nothing.
        self.assertEqual((Journal.objects.count(), self.approve(item)['result'], EntityGroup.objects.count()), (journals, done['result'], 1))

    def test_accounts_that_exist_in_only_one_book_or_differ_by_name_are_kept_apart_and_shown(self):
        revision = ledger.read_book(self.user, self.shop['id'])['revision']
        ledger.create_account(self.user, self.shop['id'], {'expected_revision': revision, 'code': '6300', 'name': 'Shop rent', 'category': 'expense'})
        ledger.create_account(self.user, self.holding['id'], {'expected_revision': ledger.read_book(self.user, self.holding['id'])['revision'],
                                                             'code': '6300', 'name': 'Head office rent', 'category': 'expense'})
        ledger.create_account(self.user, self.shop['id'], {'expected_revision': revision + 1, 'code': '1400', 'name': 'Stock', 'category': 'asset'})
        self.entry(self.shop, '2026-03-25', 'Shop rent', (('6300', '400', '0'), ('1000', '0', '400')))
        self.entry(self.holding, '2026-03-26', 'Office rent', (('6300', '700', '0'), ('1000', '0', '700')))
        self.entry(self.shop, '2026-03-27', 'Stock bought', (('1400', '150', '0'), ('1000', '0', '150')))
        report = self.combined(self.grouped())
        self.assertEqual(report['income_statement']['expenses'], [
            {'code': '6200', 'name': 'Office expenses', 'amounts': ['900.0000', '300.0000'], 'total': '1200.0000'},
            {'code': '6300', 'name': 'Head office rent', 'amounts': ['700.0000', '0.0000'], 'total': '700.0000'},
            {'code': '6300', 'name': 'Shop rent', 'amounts': ['0.0000', '400.0000'], 'total': '400.0000'}])
        self.assertEqual(report['balance_sheet']['assets'], [
            {'code': '1000', 'name': 'Checking', 'amounts': ['3400.0000', '700.5000'], 'total': '4100.5000'},
            {'code': '1400', 'name': 'Stock', 'amounts': ['0.0000', '150.0000'], 'total': '150.0000'}])
        self.assertEqual(report['income_statement']['total_expenses'], {'amounts': ['1600.0000', '700.0000'], 'total': '2300.0000'})

    def test_groups_of_other_people_mixed_currencies_and_malformed_requests_are_refused_before_anything_is_saved(self):
        cases = ((dict(book_ids=[self.holding['id']]), 'group_needs_two_books'), (dict(book_ids=[self.holding['id'], self.holding['id']]), 'group_book_repeated'),
                 (dict(book_ids=[self.holding['id'], self.euro['id']]), 'group_currency_differs'), (dict(book_ids=[self.holding['id'], self.theirs['id']]), 'ledger_not_found'),
                 (dict(book_ids=[self.holding['id'], str(uuid.uuid4())]), 'ledger_not_found'), (dict(book_ids=[self.holding['id'], 'shop']), None),
                 (dict(book_ids='all'), None), (dict(book_ids=[]), 'group_needs_two_books'), (dict(name=''), None), (dict(name=' padded '), None),
                 (dict(name='x' * 121), None), (dict(name=5), None), (dict(force=True), None))
        for changes, code in cases:
            with self.assertRaises(approvals.ApprovalError, msg=changes) as refused:
                self.ask(**changes)
            if code:
                self.assertEqual(refused.exception.code, code, changes)
        with self.assertRaises(approvals.ApprovalError):
            self.ask(self.other)
        self.assertEqual((ActionRequest.objects.count(), EntityGroup.objects.count()), (0, 0))
        group = self.grouped()
        self.assertEqual(groups.list_groups(self.other), {'groups': []})
        for call in (lambda: self.combined(group, user=self.other), lambda: self.combined(str(uuid.uuid4())), lambda: self.combined('group'),
                     lambda: self.combined(group, start='2025-12-01'), lambda: self.combined(group, end='2027-01-31'), lambda: self.combined(group, start='2026-04-01'),
                     lambda: groups.combined_report(self.user, group, {'from': '2026-01-01'}),
                     lambda: groups.combined_report(self.user, group, {'from': '2026-01-01', 'to': '2026-03-31', 'eliminate': True})):
            with self.assertRaises(ledger.LedgerError):
                call()
        with self.assertRaises(approvals.ApprovalError) as refused:
            self.ask()
        self.assertEqual(refused.exception.code, 'group_already_exists')
        with self.assertRaises(approvals.ApprovalError):
            approvals.request_action(self.other, {'operation': 'group.remove', 'summary': 'Remove theirs', 'payload': {'group_id': group}})

    def test_a_group_is_removed_only_by_approval_and_its_books_are_untouched(self):
        group = self.grouped()
        stop = approvals.request_action(self.user, {'operation': 'group.remove', 'summary': 'Remove the group', 'payload': {'group_id': group}})
        self.assertEqual(stop['detail'], {'name': 'Synthetic family of companies', 'currency': 'USD', 'books': ['Synthetic Holding', 'Synthetic Shop'], 'current': True})
        self.assertEqual(self.combined(group)['income_statement']['net_income']['total'], '5050.5000')        # Still there until approved.
        journals = Journal.objects.count()
        done = self.approve(stop)
        self.assertEqual((done['state'], done['result']), ('approved', {'group_id': group, 'removed': True}))
        self.assertEqual((groups.list_groups(self.user), Journal.objects.count()), ({'groups': []}, journals))
        with self.assertRaises(ledger.LedgerError):
            self.combined(group)
        with self.assertRaises(approvals.ApprovalError) as refused:
            approvals.request_action(self.user, {'operation': 'group.remove', 'summary': 'Remove it again', 'payload': {'group_id': group}})
        self.assertEqual(refused.exception.code, 'group_not_found')
        self.assertEqual(self.approve(self.ask())['state'], 'approved')              # The same books can be grouped again.

    def test_the_agent_tool_lists_groups_and_reads_the_combined_report_but_cannot_change_a_group(self):
        group = self.grouped()
        self.book = self.holding                      # The shared broker helper presents one book.
        broker, _, _ = BookkeepingPostingTests.broker(self)
        tool = lambda arguments: broker.call('maipa_books_groups', arguments)
        self.assertEqual(json.loads(tool({})['content'][0]['text']), groups.list_groups(self.user))
        self.assertEqual(json.loads(tool({'group_id': group, 'from': '2026-01-01', 'to': '2026-03-31'})['content'][0]['text']), self.combined(group))
        for bad in ({'group_id': group}, {'group_id': group, 'from': '2026-01-01'}, {'from': '2026-01-01', 'to': '2026-03-31'},
                    {'group_id': str(uuid.uuid4()), 'from': '2026-01-01', 'to': '2026-03-31'}, {'name': 'New group', 'book_ids': []}, {'group_id': group, 'remove': True}):
            self.assertTrue(tool(bad)['isError'], bad)
        self.assertEqual(EntityGroup.objects.count(), 1)


class GroupEliminationTests(TestCase):
    """Dealings between grouped businesses are removed only for accounts the person approved, and only called eliminated when they cancel out."""
    setUpTestData = classmethod(EntityGroupTests.setUpTestData.__func__)
    setUp, entry, ask, approve, grouped, combined = (getattr(EntityGroupTests, name) for name in ("setUp", "entry", "ask", "approve", "grouped", "combined"))

    def account(self, book, code, name, category):
        ledger.create_account(self.user, book['id'], {'expected_revision': ledger.read_book(self.user, book['id'])['revision'], 'code': code, 'name': name, 'category': category})
        return next(a['id'] for a in ledger.read_book(self.user, book['id'])['accounts'] if a['code'] == code)

    def dealings(self, shop_records_fee=True):
        self.due_from = self.account(self.holding, '1300', 'Due from Shop', 'asset')
        self.fees_in = self.account(self.holding, '4100', 'Management fees', 'income')
        self.due_to = self.account(self.shop, '2300', 'Due to Holding', 'liability')
        self.fees_out = self.account(self.shop, '6400', 'Management fees paid', 'expense')
        self.entry(self.holding, '2026-02-15', 'Loan to shop', (('1300', '500', '0'), ('1000', '0', '500')))
        self.entry(self.shop, '2026-02-15', 'Loan from holding', (('1000', '500', '0'), ('2300', '0', '500')))
        self.entry(self.holding, '2026-03-30', 'Management fee charged', (('1300', '200', '0'), ('4100', '0', '200')))
        if shop_records_fee:
            self.entry(self.shop, '2026-03-30', 'Management fee owed', (('6400', '200', '0'), ('2300', '0', '200')))

    def mark(self, group, accounts, user=None):
        return approvals.request_action(user or self.user, {'operation': 'group.eliminate', 'summary': 'Remove dealings between the two businesses',
                                                            'payload': {'group_id': group, 'account_ids': accounts}})

    def test_approved_accounts_are_removed_from_the_totals_and_the_report_is_eliminated_when_they_cancel(self):
        self.dealings()
        group = self.grouped()
        before = self.combined(group)
        self.assertEqual((before['eliminated'], before['consolidated']), (False, None))
        item = self.mark(group, [self.due_from, self.fees_in, self.due_to, self.fees_out])
        self.assertEqual(item['detail'], {'name': 'Synthetic family of companies', 'current': True, 'accounts': [
            {'business': 'Synthetic Holding', 'account': '1300 Due from Shop', 'category': 'asset'}, {'business': 'Synthetic Holding', 'account': '4100 Management fees', 'category': 'income'},
            {'business': 'Synthetic Shop', 'account': '2300 Due to Holding', 'category': 'liability'}, {'business': 'Synthetic Shop', 'account': '6400 Management fees paid', 'category': 'expense'}]})
        self.assertEqual(self.combined(group)['consolidated'], None)                  # Nothing is removed until approved.
        journals = Journal.objects.count()
        done = self.approve(item)
        self.assertEqual((done['state'], done['result']), ('approved', {'group_id': group, 'accounts': 4}))
        report = self.combined(group)
        # The side-by-side figures are unchanged; the consolidated totals leave the approved accounts out.
        same = ('income_statement', 'balance_sheet', 'cash', 'books')
        self.assertEqual({key: report[key] for key in same}, {key: before[key] for key in same})
        self.assertEqual(report['income_statement']['total_income']['total'], '6450.5000')
        self.assertEqual(report['balance_sheet']['total_assets']['total'], '6050.5000')
        self.assertEqual(report['consolidated'], {
            'removed': [{'book_id': self.holding['id'], 'business': 'Synthetic Holding', 'code': '1300', 'name': 'Due from Shop', 'category': 'asset', 'amount': '700.0000'},
                        {'book_id': self.holding['id'], 'business': 'Synthetic Holding', 'code': '4100', 'name': 'Management fees', 'category': 'income', 'amount': '200.0000'},
                        {'book_id': self.shop['id'], 'business': 'Synthetic Shop', 'code': '2300', 'name': 'Due to Holding', 'category': 'liability', 'amount': '700.0000'},
                        {'book_id': self.shop['id'], 'business': 'Synthetic Shop', 'code': '6400', 'name': 'Management fees paid', 'category': 'expense', 'amount': '200.0000'}],
            'total_income': '6250.5000', 'total_expenses': '1200.0000', 'net_income': '5050.5000',
            'total_assets': '5350.5000', 'total_liabilities': '300.0000', 'total_equity': '0.0000', 'earnings_to_date': '5050.5000',
            'income_difference': '0.0000', 'balance_difference': '0.0000'})
        self.assertEqual(report['eliminated'], True)
        self.assertEqual(Journal.objects.count(), journals)
        # An empty list, approved, clears the marks.
        self.approve(self.mark(group, []))
        cleared = self.combined(group)
        self.assertEqual((cleared['eliminated'], cleared['consolidated']), (False, None))

    def test_dealings_recorded_on_one_side_only_are_shown_as_a_difference_and_not_called_eliminated(self):
        self.dealings(shop_records_fee=False)
        group = self.grouped()
        self.approve(self.mark(group, [self.due_from, self.fees_in, self.due_to, self.fees_out]))
        report = self.combined(group)
        self.assertEqual((report['eliminated'], report['consolidated']['income_difference'], report['consolidated']['balance_difference']), (False, '200.0000', '200.0000'))
        self.assertEqual([(row['code'], row['amount']) for row in report['consolidated']['removed']], [('1300', '700.0000'), ('4100', '200.0000'), ('2300', '500.0000'), ('6400', '0.0000')])
        # Before the fee, the loan alone cancels out.
        february = self.combined(group, end='2026-02-28')
        self.assertEqual((february['eliminated'], february['consolidated']['balance_difference'], february['consolidated']['total_assets']), (True, '0.0000', '5000.0000'))

    def test_accounts_outside_the_group_repeats_and_other_people_are_refused_and_nothing_is_marked(self):
        self.dealings()
        group = self.grouped()
        outside = next(a['id'] for a in ledger.read_book(self.user, self.euro['id'])['accounts'] if a['code'] == '1000')
        theirs = next(a['id'] for a in ledger.read_book(self.other, self.theirs['id'])['accounts'] if a['code'] == '1000')
        for accounts, code in (([outside], 'group_account_outside'), ([theirs], 'group_account_outside'), ([str(uuid.uuid4())], 'group_account_outside'),
                               ([self.due_from, self.due_from], 'group_account_repeated'), (['1300'], None), ('all', None), ([str(uuid.uuid4()) for _ in range(101)], None)):
            with self.assertRaises(approvals.ApprovalError, msg=accounts) as refused:
                self.mark(group, accounts)
            if code:
                self.assertEqual(refused.exception.code, code, accounts)
        for call in (lambda: self.mark(group, [self.due_from], self.other), lambda: self.mark(str(uuid.uuid4()), [self.due_from]),
                     lambda: approvals.request_action(self.user, {'operation': 'group.eliminate', 'summary': 'Padded', 'payload': {'group_id': group, 'account_ids': [], 'force': True}})):
            with self.assertRaises(approvals.ApprovalError):
                call()
        self.assertEqual((ActionRequest.objects.filter(operation='group.eliminate').count(), self.combined(group)['consolidated']), (0, None))
