"""Month-end close review: what still stands between the books and closing them through a date. Read-only."""
import json
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.ledger import services as ledger
from backend.maipa.ledger.models import LedgerEvent
from backend.maipa.workspace.models import Installation, Membership

PERIOD = {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-06-30'}
MARCH = 'date,description,amount\n2026-03-02,Client payment,1250.5\n2026-03-05,Synthetic rent,-900\n2026-03-31,Card fee,-10\n'


class CloseReviewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='close-owner')
        cls.other = get_user_model().objects.create_user(username='close-other')
        Membership.objects.create(user=cls.user, installation=install, role='preparer')
        Membership.objects.create(user=cls.other, installation=install, role='administrator')
        cls.entity = Entity.objects.create(engagement=Engagement.objects.create(
            owner=cls.user, installation=install, name='Synthetic closes'), name='Synthetic close business')

    def setUp(self):
        self.book = ledger.create_book(self.user, self.entity.pk, PERIOD)
        self.accounts = {a['code']: a['id'] for a in self.book['accounts']}

    def revision(self):
        return ledger.read_book(self.user, self.book['id'])['revision']

    def statement(self, content=MARCH, name='march.csv', **extra):
        base = {'account_id': self.accounts['1000'], 'opening_balance': '1000', 'closing_balance': '1340.5'}
        return ledger.import_statement(self.user, self.book['id'], {'expected_revision': self.revision(), 'filename': name, 'content': content,
                                                                   **{k: v for k, v in (base | extra).items() if v is not None}})

    def row(self, description):
        return next(r for r in ledger.source_rows(self.user, self.book['id'])['rows'] if r['description'] == description)

    def entry(self, date, description, lines, row=None, post=True):
        reference = row['id'] if row else f'synthetic-{uuid.uuid4()}'
        made = ledger.propose_journal(self.user, self.book['id'], {
            'operation_id': str(uuid.uuid4()), 'expected_revision': self.revision(), 'date': date, 'description': description,
            'source': {'kind': 'csv' if row else 'manual', 'reference': reference}, 'evidence': [reference],
            'lines': [{'account_id': self.accounts[code], 'debit': debit, 'credit': credit} for code, debit, credit in lines]})
        if post:
            ledger.confirm_own_journal(self.user, self.book['id'], made['id'],
                {'expected_revision': self.revision(), 'payload_hash': made['payload_hash'], 'confirm': True})
        return made

    def review(self, through='2026-03-31', user=None):
        return ledger.close_review(user or self.user, self.book['id'], {'through': through})

    def checks(self, result):
        return {item['key']: item for item in result['checks']}

    def clean_march(self):
        ledger.post_opening(self.user, self.book['id'], {'expected_revision': self.revision(),
            'balances': [{'account_id': self.accounts['1000'], 'debit': '1000', 'credit': '0'}], 'control': {'debit': '1000', 'credit': '0'}})
        saved = self.statement()
        self.entry('2026-03-02', 'Client payment', [('1000', '1250.5', '0'), ('4000', '0', '1250.5')], self.row('Client payment'))
        self.entry('2026-03-05', 'Synthetic rent', [('6200', '900', '0'), ('1000', '0', '900')], self.row('Synthetic rent'))
        self.entry('2026-03-31', 'Card fee', [('6100', '10', '0'), ('1000', '0', '10')], self.row('Card fee'))
        ledger.record_reconciliation(self.user, self.book['id'], {'expected_revision': self.revision(), 'import_id': saved['id']})
        return saved

    def test_books_with_everything_settled_are_ready_and_the_calendar_shows_each_month(self):
        self.clean_march()
        before = (self.revision(), LedgerEvent.objects.count())
        result = self.review()
        self.assertEqual({key: result[key] for key in ('book_id', 'currency', 'through', 'period_start', 'period_end', 'closed_through', 'can_close', 'blocking', 'ready')},
                         {'book_id': self.book['id'], 'currency': 'USD', 'through': '2026-03-31', 'period_start': '2026-01-01', 'period_end': '2026-06-30',
                          'closed_through': None, 'can_close': True, 'blocking': None, 'ready': True})
        self.assertEqual([(c['key'], c['ok'], c['count'], c['items']) for c in result['checks']],
                         [('drafts', True, 0, []), ('statement_lines', True, 0, []), ('reconciliations', True, 0, []), ('integrity', True, 0, [])])
        self.assertEqual(result['calendar'], [{'month': f'2026-0{n}', 'ends': ends, 'state': 'open'} for n, ends in
                                              ((1, '2026-01-31'), (2, '2026-02-28'), (3, '2026-03-31'), (4, '2026-04-30'), (5, '2026-05-31'), (6, '2026-06-30'))])
        self.assertEqual((self.revision(), LedgerEvent.objects.count()), before)       # Read-only.
        self.assertEqual(self.review(), result)
        ledger.change_period(self.user, self.book['id'], {'expected_revision': self.revision(), 'action': 'close', 'through': '2026-02-28'})
        later = self.review()
        self.assertEqual(([m['state'] for m in later['calendar']], later['closed_through'], later['ready']),
                         (['closed', 'closed', 'open', 'open', 'open', 'open'], '2026-02-28', True))

    def test_each_outstanding_item_is_listed_under_its_check_with_exact_figures(self):
        saved = self.statement()
        draft = self.entry('2026-03-05', 'Synthetic rent', [('6200', '900', '0'), ('1000', '0', '900')], self.row('Synthetic rent'), post=False)
        self.entry('2026-04-02', 'April draft', [('6200', '5', '0'), ('1000', '0', '5')], post=False)      # After the date: not this close's concern.
        self.statement('date,description,amount\n2026-03-15,Loose line,5\n', 'loose.csv', account_id=None, opening_balance=None, closing_balance=None)
        self.statement('date,description,amount\n2026-04-10,April line,7\n', 'april.csv', opening_balance=None, closing_balance=None)
        found = self.checks(self.review())
        self.assertEqual((found['drafts']['ok'], found['drafts']['count'], found['drafts']['items']),
                         (False, 1, [{'journal_id': draft['id'], 'sequence': draft['sequence'], 'date': '2026-03-05', 'description': 'Synthetic rent', 'amount': '900.0000'}]))
        self.assertEqual((found['statement_lines']['ok'], found['statement_lines']['count']), (False, 4))
        self.assertEqual([(i['date'], i['description'], i['amount'], i['status']) for i in found['statement_lines']['items']],
                         [('2026-03-02', 'Client payment', '1250.5000', 'pending'), ('2026-03-05', 'Synthetic rent', '-900.0000', 'proposed'),
                          ('2026-03-15', 'Loose line', '5.0000', 'pending'), ('2026-03-31', 'Card fee', '-10.0000', 'pending')])
        self.assertEqual((found['reconciliations']['ok'], found['reconciliations']['count']), (False, 2))
        self.assertEqual([(i['filename'], i['state'], i['last_date']) for i in found['reconciliations']['items']],
                         [('march.csv', 'difference', '2026-03-31'), ('loose.csv', 'needs_account', '2026-03-15')])
        self.assertEqual(found['reconciliations']['items'][0]['import_id'], saved['id'])
        self.assertEqual((found['integrity']['ok'], found['integrity']['count']), (True, 0))
        result = self.review()
        self.assertEqual((result['can_close'], result['blocking'], result['ready']), (False, 'ledger_period_has_drafts', False))

    def test_reconciliation_states_cover_missing_balances_unrecorded_and_changed_since_recording(self):
        saved = self.clean_march()
        states = lambda: {i['filename']: i['state'] for i in self.checks(self.review())['reconciliations']['items']}
        self.assertEqual(states(), {})
        # A statement of a second bank account, so the recorded March reconciliation of Checking is not affected.
        ledger.create_account(self.user, self.book['id'], {'expected_revision': self.revision(), 'code': '1010', 'name': 'Savings', 'category': 'asset'})
        savings = next(a['id'] for a in ledger.read_book(self.user, self.book['id'])['accounts'] if a['code'] == '1010')
        ledger.set_account_cash(self.user, self.book['id'], savings, {'expected_revision': self.revision(), 'cash': True})
        bare = self.statement('date,description,amount\n2026-03-20,Second account line,3\n', 'bare.csv', account_id=savings, opening_balance=None, closing_balance=None)
        self.assertEqual(states(), {'bare.csv': 'needs_balances'})
        # A new unposted line on Checking itself changes what its recorded reconciliation was based on.
        self.statement('date,description,amount\n2026-03-28,Late Checking line,2\n', 'late.csv', opening_balance=None, closing_balance=None)
        self.assertEqual(states(), {'march.csv': 'changed', 'bare.csv': 'needs_balances', 'late.csv': 'needs_balances'})
        fee = next(j for j in ledger.read_book(self.user, self.book['id'])['journals'] if j['description'] == 'Card fee')
        ledger.reverse_journal(self.user, self.book['id'], fee['id'], {'expected_revision': self.revision(), 'date': '2026-03-31'})
        self.assertEqual(states(), {'march.csv': 'changed', 'bare.csv': 'needs_balances', 'late.csv': 'needs_balances'})
        self.assertEqual(self.checks(self.review())['reconciliations']['items'][0]['import_id'], saved['id'])
        self.assertEqual(bare['filename'], 'bare.csv')
        other = self.statement('date,description,amount\n2026-03-25,Agreeing line,4\n', 'agrees.csv', opening_balance='0', closing_balance='4', account_id=None)
        self.assertEqual(states()['agrees.csv'], 'needs_account')
        self.assertEqual(other['filename'], 'agrees.csv')

    def test_a_date_that_cannot_be_closed_says_why_and_bad_requests_and_foreign_readers_are_refused(self):
        self.clean_march()
        ledger.change_period(self.user, self.book['id'], {'expected_revision': self.revision(), 'action': 'close', 'through': '2026-03-31'})
        again = self.review('2026-03-31')
        self.assertEqual((again['can_close'], again['blocking'], again['ready']), (False, 'ledger_period_already_closed', False))
        earlier = self.review('2026-02-28')
        self.assertEqual((earlier['can_close'], earlier['blocking']), (False, 'ledger_period_already_closed'))
        self.assertEqual((self.review('2026-04-30')['can_close'], self.review('2026-04-30')['blocking']), (True, None))
        for value in ({'through': '2026-07-01'}, {'through': '2025-12-31'}, {'through': 'March'}, {'through': '2026-02-30'}, {},
                      {'through': '2026-04-30', 'force': True}, {'through': None}):
            with self.assertRaises(ledger.LedgerError, msg=value):
                ledger.close_review(self.user, self.book['id'], value)
        with self.assertRaises(ledger.LedgerError):
            self.review('2026-04-30', self.other)
        with self.assertRaises(ledger.LedgerError):
            ledger.close_review(self.user, str(uuid.uuid4()), {'through': '2026-04-30'})

    def test_http_and_the_agent_tool_return_exactly_the_service_result(self):
        from tests.test_bookkeeping_posting import BookkeepingPostingTests
        self.clean_march()
        self.client.force_login(self.user)
        actor = {'HTTP_X_MAIPA_AGENT_ACTOR': str(self.user.pk)}
        url = f"/api/books/{self.book['id']}/close-review/"
        answer = self.client.get(url + '?through=2026-03-31', secure=True, **actor)
        self.assertEqual((answer.status_code, answer.json()), (200, self.review()))
        for bad in ('', '?through=March', '?through=2026-03-31&force=1', '?through=2026-07-01'):
            self.assertEqual(self.client.get(url + bad, secure=True, **actor).status_code, 400, bad)
        self.assertEqual(self.client.get(url + '?through=2026-03-31', secure=True).status_code, 409)
        self.assertEqual(self.client.post(url + '?through=2026-03-31', data='{}', content_type='application/json', secure=True, **actor).status_code, 405)
        broker, _, _ = BookkeepingPostingTests.broker(self)
        tool = lambda arguments: broker.call('maipa_books_close_review', {'book_id': self.book['id'], **arguments})
        self.assertEqual(json.loads(tool({'through': '2026-03-31'})['content'][0]['text']), self.review())
        self.assertTrue(tool({})['isError'])
        self.assertTrue(tool({'through': '2026-03-31', 'close': True})['isError'])
