"""Account activity: posted lines in order with exact running balances; pending kept apart."""
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.maipa.ledger import services
from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.workspace.models import Installation, Membership


class AccountActivityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='activity-owner')
        cls.other = get_user_model().objects.create_user(username='activity-other')
        cls.member = Membership.objects.create(user=cls.user, installation=install, role='preparer')
        Membership.objects.create(user=cls.other, installation=install, role='administrator')
        engagement = Engagement.objects.create(owner=cls.user, installation=install, name='Synthetic activity')
        cls.entity = Entity.objects.create(engagement=engagement, name='Synthetic activity business')
        sibling = Engagement.objects.create(owner=cls.user, installation=install, name='Synthetic sibling')
        cls.sibling = Entity.objects.create(engagement=sibling, name='Synthetic sibling business')

    def setUp(self):
        self.book = services.create_book(self.user, self.entity.pk,
            {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'})
        self.accounts = {a['code']: a['id'] for a in self.book['accounts']}

    def propose(self, day, reference, debit_code, credit_code, amount):
        revision = services.read_book(self.user, self.book['id'])['revision']
        return services.propose_journal(self.user, self.book['id'], {
            'operation_id': str(uuid.uuid4()), 'expected_revision': revision, 'date': day,
            'description': f'Synthetic {reference}', 'source': {'kind': 'manual', 'reference': reference},
            'evidence': [reference], 'lines': [
                {'account_id': self.accounts[debit_code], 'debit': amount, 'credit': '0'},
                {'account_id': self.accounts[credit_code], 'debit': '0', 'credit': amount}]})

    def post(self, entry):
        revision = services.read_book(self.user, self.book['id'])['revision']
        services.confirm_own_journal(self.user, self.book['id'], entry['id'],
            {'expected_revision': revision, 'payload_hash': entry['payload_hash'], 'confirm': True})

    def test_posted_lines_run_in_date_then_sequence_order_with_exact_balances_and_pending_apart(self):
        later = self.propose('2026-03-05', 'synthetic-later', '6200', '1000', '0.20')
        earlier = self.propose('2026-03-01', 'synthetic-earlier', '1000', '3000', '0.30')
        pending = self.propose('2026-02-01', 'synthetic-pending', '6200', '1000', '0.10')
        self.post(later); self.post(earlier)
        activity = services.account_activity(self.user, self.book['id'], self.accounts['1000'])
        self.assertEqual((activity['account']['code'], activity['currency'], activity['normal_side']), ('1000', 'USD', 'debit'))
        self.assertEqual([(row['journal_id'], row['date'], row['debit'], row['credit'], row['balance']) for row in activity['posted']],
                         [(earlier['id'], '2026-03-01', '0.3000', '0.0000', '0.3000'),
                          (later['id'], '2026-03-05', '0.0000', '0.2000', '0.1000')])
        self.assertEqual(activity['posted'][0]['source'], {'kind': 'manual', 'reference': 'synthetic-earlier'})
        self.assertEqual((activity['posted_debit'], activity['posted_credit'], activity['balance']), ('0.3000', '0.2000', '0.1000'))
        self.assertEqual([(row['journal_id'], row['credit']) for row in activity['pending']], [(pending['id'], '0.1000')])
        trial = {a['code']: a for a in services.journal_preview(self.user, self.book['id'])['accounts']}
        self.assertEqual(trial['1000']['balance_debit'], activity['balance'])

    def test_credit_normal_account_balance_is_positive_on_its_normal_side(self):
        self.post(self.propose('2026-04-01', 'synthetic-capital', '1000', '3000', '125.5'))
        activity = services.account_activity(self.user, self.book['id'], self.accounts['3000'])
        self.assertEqual((activity['normal_side'], activity['balance']), ('credit', '125.5000'))
        self.assertEqual(activity['posted'][0]['balance'], '125.5000')

    def test_foreign_unknown_and_sibling_book_accounts_are_not_readable(self):
        sibling = services.create_book(self.user, self.sibling.pk,
            {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'})
        for user, book_id, account_id in (
                (self.other, self.book['id'], self.accounts['1000']),
                (self.user, self.book['id'], sibling['accounts'][0]['id']),
                (self.user, self.book['id'], str(uuid.uuid4())),
                (self.user, self.book['id'], 'not-a-uuid')):
            with self.assertRaises(services.LedgerError):
                services.account_activity(user, book_id, account_id)

    def test_http_activity_is_owner_bound_read_only_and_matches_the_service(self):
        self.post(self.propose('2026-05-01', 'synthetic-http', '6200', '1000', '9.99'))
        url = f"/api/books/{self.book['id']}/accounts/{self.accounts['1000']}/activity/"
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(url, secure=True).status_code, 409)  # Actor binding required.
        response = self.client.get(url, secure=True, HTTP_X_MAIPA_AGENT_ACTOR=str(self.user.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), services.account_activity(self.user, self.book['id'], self.accounts['1000']))
        self.assertEqual(response.json()['balance'], '-9.9900')
        self.assertEqual(self.client.post(url, data='{}', content_type='application/json', secure=True,
                                          HTTP_X_MAIPA_AGENT_ACTOR=str(self.user.pk)).status_code, 405)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url, secure=True, HTTP_X_MAIPA_AGENT_ACTOR=str(self.other.pk)).status_code, 404)
