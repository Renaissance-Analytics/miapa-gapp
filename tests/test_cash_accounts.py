"""Bank and cash accounts are marked explicitly, by approval, and only on asset accounts."""
import importlib
import uuid

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.maipa.approvals import services as approvals
from backend.maipa.approvals.models import ActionRequest
from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.ledger import services as ledger
from backend.maipa.ledger.models import Account, LedgerEvent
from backend.maipa.workspace.models import Installation, Membership

PERIOD = {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'}


class CashAccountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='cash-owner')
        cls.other = get_user_model().objects.create_user(username='cash-other')
        Membership.objects.create(user=cls.user, installation=install, role='preparer')
        Membership.objects.create(user=cls.other, installation=install, role='administrator')
        engagement = Engagement.objects.create(owner=cls.user, installation=install, name='Synthetic cash')
        cls.entity = Entity.objects.create(engagement=engagement, name='Synthetic cash business')
        cls.second = Entity.objects.create(engagement=engagement, name='Synthetic other business')

    def setUp(self):
        self.book = ledger.create_book(self.user, self.entity.pk, PERIOD)
        self.accounts = {a['code']: a['id'] for a in self.book['accounts']}

    def ask(self, code='1500', cash=True, user=None, **changes):
        payload = {'book_id': self.book['id'], 'account_id': self.accounts[code], 'cash': cash} | changes
        return approvals.request_action(user or self.user, {'operation': 'account.cash', 'summary': 'Mark the account', 'payload': payload})

    def approve(self, item):
        return approvals.decide(self.user, item['id'], {'decision': 'approve', 'payload_hash': item['payload_hash']})

    def flags(self):
        return {a['code']: a['cash'] for a in ledger.read_book(self.user, self.book['id'])['accounts']}

    def test_new_books_mark_only_checking_and_every_account_reports_its_flag(self):
        flags = self.flags()
        self.assertEqual([code for code, cash in flags.items() if cash], ['1000'])
        self.assertEqual(len(flags), 12)
        report = ledger.journal_preview(self.user, self.book['id'])
        self.assertEqual({a['code']: a['cash'] for a in report['accounts']}, flags)
        made = ledger.create_account(self.user, self.book['id'], {'expected_revision': 0, 'code': '1010', 'name': 'Savings', 'category': 'asset'})
        self.assertEqual(made['cash'], False)

    def test_approved_request_marks_and_unmarks_an_asset_account_with_a_recorded_event(self):
        item = self.ask()
        self.assertEqual(item['detail'], {'books': 'Synthetic cash business', 'account': '1500 Equipment', 'cash': False})
        self.assertEqual((self.flags()['1500'], ledger.read_book(self.user, self.book['id'])['revision']), (False, 0))
        done = self.approve(item)
        self.assertEqual((done['state'], done['result']), ('approved', {'id': self.accounts['1500'], 'code': '1500', 'name': 'Equipment', 'category': 'asset', 'cash': True}))
        self.assertEqual((self.flags()['1500'], ledger.read_book(self.user, self.book['id'])['revision']), (True, 1))
        event = LedgerEvent.objects.get(book_id=self.book['id'], revision=1)
        self.assertEqual((event.action, str(event.target)), ('account.cash', self.accounts['1500']))
        self.assertEqual(self.approve(item)['result'], done['result'])
        self.assertEqual(ledger.read_book(self.user, self.book['id'])['revision'], 1)
        self.approve(self.ask(cash=False))
        self.assertEqual([code for code, cash in self.flags().items() if cash], ['1000'])
        self.assertEqual(ledger.verify_book(self.user, self.book['id'])['ok'], True)

    def test_non_asset_unknown_foreign_unchanged_and_malformed_requests_are_refused_and_not_recorded(self):
        foreign = ledger.create_book(self.user, self.second.pk, PERIOD)['accounts'][0]['id']
        for arguments in ({'code': '2000'}, {'code': '4000'}, {'code': '3900'}, {'code': '1000'}, {'code': '1500', 'cash': False},
                          {'cash': 'yes'}, {'cash': 1}, {'cash': None}, {'account_id': foreign}, {'account_id': str(uuid.uuid4())},
                          {'account_id': 'checking'}, {'note': 'x'}, {'user': self.other}):
            with self.assertRaises(approvals.ApprovalError, msg=arguments):
                self.ask(**arguments)
        self.assertEqual(ActionRequest.objects.count(), 0)
        self.assertEqual([code for code, cash in self.flags().items() if cash], ['1000'])

    def test_a_stale_request_fails_without_changing_the_account(self):
        first, second = self.ask(), self.ask()
        self.approve(first)
        stale = self.approve(second)
        self.assertEqual((stale['state'], stale['error']), ('failed', 'ledger_cash_unchanged'))
        self.assertEqual(ledger.read_book(self.user, self.book['id'])['revision'], 1)

    def test_existing_books_get_only_an_unrenamed_starter_checking_account_marked(self):
        Account.objects.filter(book_id=self.book['id']).update(cash=False)
        renamed = ledger.create_book(self.user, self.second.pk, PERIOD)
        Account.objects.filter(book_id=renamed['id']).update(cash=False)
        Account.objects.filter(book_id=renamed['id'], code='1000').update(name='Petty float')
        migration = importlib.import_module('backend.maipa.ledger.migrations.0005_cash_accounts')
        from types import SimpleNamespace
        from django.db import connection
        migration.mark_starter_checking(apps, SimpleNamespace(connection=connection))
        self.assertEqual([code for code, cash in self.flags().items() if cash], ['1000'])
        self.assertEqual(Account.objects.filter(book_id=renamed['id'], cash=True).count(), 0)
