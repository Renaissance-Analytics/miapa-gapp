"""Controlled report release: statements for a closed period are frozen by approval, and later changes to the books are shown, never hidden."""
import json
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.maipa.approvals import services as approvals
from backend.maipa.approvals.models import ActionRequest
from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.ledger import services as ledger
from backend.maipa.ledger.models import LedgerEvent, ReportRelease
from backend.maipa.workspace.models import Installation, Membership
from tests.test_bookkeeping_posting import BookkeepingPostingTests

PERIOD = {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'}


class ReportReleaseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='release-owner')
        cls.other = get_user_model().objects.create_user(username='release-other')
        Membership.objects.create(user=cls.user, installation=install, role='preparer')
        Membership.objects.create(user=cls.other, installation=install, role='administrator')
        cls.entity = Entity.objects.create(engagement=Engagement.objects.create(
            owner=cls.user, installation=install, name='Synthetic releases'), name='Synthetic release business')

    def setUp(self):
        self.book = ledger.create_book(self.user, self.entity.pk, PERIOD)
        self.accounts = {a['code']: a['id'] for a in self.book['accounts']}
        self.entry('2026-02-10', 'Client payment', (('1000', '5000', '0'), ('4000', '0', '5000')))
        self.entry('2026-03-05', 'Synthetic rent', (('6200', '900', '0'), ('1000', '0', '900')))

    def revision(self):
        return ledger.read_book(self.user, self.book['id'])['revision']

    def entry(self, date, description, lines):
        made = ledger.propose_journal(self.user, self.book['id'], {
            'operation_id': str(uuid.uuid4()), 'expected_revision': self.revision(), 'date': date, 'description': description,
            'source': {'kind': 'manual', 'reference': f'synthetic-{uuid.uuid4()}'}, 'evidence': ['synthetic'],
            'lines': [{'account_id': self.accounts[code], 'debit': debit, 'credit': credit} for code, debit, credit in lines]})
        ledger.confirm_own_journal(self.user, self.book['id'], made['id'], {'expected_revision': self.revision(), 'payload_hash': made['payload_hash'], 'confirm': True})

    def period(self, action, through):
        ledger.change_period(self.user, self.book['id'], {'expected_revision': self.revision(), 'action': action, 'through': through})

    def ask(self, user=None, **changes):
        payload = {'book_id': self.book['id'], 'from': '2026-01-01', 'to': '2026-03-31', 'title': 'First quarter statements', **changes}
        return approvals.request_action(user or self.user, {'operation': 'report.release', 'summary': 'Release the first quarter statements', 'payload': payload})

    def approve(self, item):
        return approvals.decide(self.user, item['id'], {'decision': 'approve', 'payload_hash': item['payload_hash']})

    def listed(self, **value):
        return ledger.report_releases(self.user, self.book['id'], value)

    def test_an_approved_release_freezes_the_statements_of_a_closed_period(self):
        self.period('close', '2026-03-31')
        report = ledger.financial_report(self.user, self.book['id'], {'from': '2026-01-01', 'to': '2026-03-31'})
        item = self.ask()
        self.assertEqual(item['detail'], {'books': 'Synthetic release business', 'currency': 'USD', 'title': 'First quarter statements',
            'from': '2026-01-01', 'to': '2026-03-31', 'number': 1, 'current': True,
            'totals': {'total_income': '5000.0000', 'total_expenses': '900.0000', 'net_income': '4100.0000', 'total_assets': '4100.0000',
                       'total_liabilities': '0.0000', 'total_equity': '0.0000', 'earnings_to_date': '4100.0000', 'closing_cash': '4100.0000'}})
        self.assertEqual(ReportRelease.objects.count(), 0)
        before = self.revision()
        done = self.approve(item)
        self.assertEqual(done['state'], 'approved', done)
        saved = ReportRelease.objects.get(pk=done['result']['release_id'])
        self.assertEqual((saved.number, saved.title, saved.period_from.isoformat(), saved.period_to.isoformat(), saved.released_by_id),
                         (1, 'First quarter statements', '2026-01-01', '2026-03-31', self.user.pk))
        self.assertEqual((self.revision(), LedgerEvent.objects.filter(action='report.release').count()), (before + 1, 1))
        listing = self.listed()
        self.assertEqual([(r['id'], r['number'], r['title'], r['from'], r['to'], r['unchanged'], r['latest']) for r in listing['releases']],
                         [(str(saved.pk), 1, 'First quarter statements', '2026-01-01', '2026-03-31', True, True)])
        one = self.listed(release_id=str(saved.pk))['release']
        self.assertEqual({key: one['statements'][key] for key in ('income_statement', 'balance_sheet', 'cash_flow')},
                         {key: report[key] for key in ('income_statement', 'balance_sheet', 'cash_flow')})
        self.assertEqual((one['unchanged'], one['changes']), (True, []))
        # The saved release cannot be edited or removed, and approving again changes nothing.
        saved.title = 'Edited'
        with self.assertRaises(ValueError):
            saved.save()
        with self.assertRaises(ValueError):
            saved.delete()
        self.assertEqual(self.approve(item)['result'], done['result'])
        with self.assertRaises(approvals.ApprovalError) as refused:
            self.ask(title='Same figures again')
        self.assertEqual(refused.exception.code, 'report_already_released')
        self.assertEqual(ReportRelease.objects.count(), 1)

    def test_a_later_change_to_the_books_is_shown_and_a_new_release_supersedes_the_old_one(self):
        self.period('close', '2026-03-31')
        first = self.approve(self.ask())['result']['release_id']
        self.period('reopen', '2026-02-28')
        self.entry('2026-03-20', 'Late supplier bill', (('6200', '250', '0'), ('2000', '0', '250')))
        self.period('close', '2026-03-31')
        listing = self.listed()['releases']
        self.assertEqual([(r['number'], r['unchanged'], r['latest']) for r in listing], [(1, False, True)])
        one = self.listed(release_id=first)['release']
        self.assertEqual(one['statements']['income_statement']['total_expenses'], '900.0000')        # Still the released figure.
        self.assertEqual(one['changes'], [{'total': 'total_expenses', 'released': '900.0000', 'now': '1150.0000'},
                                          {'total': 'net_income', 'released': '4100.0000', 'now': '3850.0000'},
                                          {'total': 'total_liabilities', 'released': '0.0000', 'now': '250.0000'},
                                          {'total': 'earnings_to_date', 'released': '4100.0000', 'now': '3850.0000'}])
        again = self.ask(title='First quarter statements, revised')
        self.assertEqual((again['detail']['number'], again['detail']['totals']['net_income']), (2, '3850.0000'))
        second = self.approve(again)['result']['release_id']
        self.assertEqual([(r['id'], r['number'], r['unchanged'], r['latest']) for r in self.listed()['releases']],
                         [(second, 2, True, True), (first, 1, False, False)])
        # A different range is its own series.
        self.approve(self.ask(**{'from': '2026-03-01', 'title': 'March statements'}))
        self.assertEqual([(r['number'], r['latest']) for r in self.listed()['releases']], [(3, True), (2, True), (1, False)])

    def test_open_periods_bad_ranges_and_other_people_are_refused_before_anything_is_saved(self):
        with self.assertRaises(approvals.ApprovalError) as refused:
            self.ask()
        self.assertEqual(refused.exception.code, 'report_period_not_closed')
        self.period('close', '2026-03-31')
        cases = ((dict(to='2026-04-30'), 'report_period_not_closed'), (dict(to='2027-01-31'), 'ledger_date_outside_period'),
                 ({'from': '2025-12-01'}, 'ledger_date_outside_period'), ({'from': '2026-04-01'}, 'ledger_date_outside_period'),
                 (dict(to='March'), None), (dict(title=''), None), (dict(title=' padded '), None), (dict(title='x' * 121), None),
                 (dict(title=7), None), (dict(force=True), None))
        for changes, code in cases:
            with self.assertRaises(approvals.ApprovalError, msg=changes) as refused:
                self.ask(**changes)
            if code:
                self.assertEqual(refused.exception.code, code, changes)
        with self.assertRaises(approvals.ApprovalError):
            self.ask(self.other)
        with self.assertRaises(ledger.LedgerError):
            ledger.report_releases(self.other, self.book['id'], {})
        for bad in ({'release_id': str(uuid.uuid4())}, {'release_id': 'x'}, {'other': 1}, None):
            with self.assertRaises(ledger.LedgerError, msg=bad):
                ledger.report_releases(self.user, self.book['id'], bad)
        self.assertEqual((ActionRequest.objects.count(), ReportRelease.objects.count()), (0, 0))
        self.assertEqual(self.listed()['releases'], [])

    def test_reopening_after_the_request_makes_the_approval_fail_with_nothing_released(self):
        self.period('close', '2026-03-31')
        item = self.ask()
        self.period('reopen', None)
        done = self.approve(item)
        self.assertEqual((done['state'], done['error'], ReportRelease.objects.count()), ('failed', 'report_period_not_closed', 0))
        self.assertEqual(approvals.list_requests(self.user)['recent'][0]['detail'], None)

    def test_the_agent_tool_reads_releases_and_cannot_make_one(self):
        self.period('close', '2026-03-31')
        saved = self.approve(self.ask())['result']['release_id']
        broker, _, _ = BookkeepingPostingTests.broker(self)
        tool = lambda arguments: broker.call('maipa_books_releases', {'book_id': self.book['id'], **arguments})
        self.assertEqual(json.loads(tool({})['content'][0]['text']), self.listed())
        self.assertEqual(json.loads(tool({'release_id': saved})['content'][0]['text']), self.listed(release_id=saved))
        self.assertTrue(tool({'release_id': str(uuid.uuid4())})['isError'])
        self.assertTrue(tool({'title': 'Release it', 'from': '2026-01-01', 'to': '2026-03-31'})['isError'])
        self.assertEqual(ReportRelease.objects.count(), 1)

    def test_the_panel_endpoint_reads_releases_for_the_owner_only_and_changes_nothing(self):
        self.period('close', '2026-03-31')
        saved = self.approve(self.ask())['result']['release_id']
        self.client.force_login(self.user)
        actor = {'HTTP_X_MAIPA_AGENT_ACTOR': str(self.user.pk)}
        url = f"/api/books/{self.book['id']}/releases/"
        before = self.revision()
        answer = self.client.get(url, secure=True, **actor)
        self.assertEqual((answer.status_code, answer.json()), (200, self.listed()))
        one = self.client.get(url + '?release=' + saved, secure=True, **actor)
        self.assertEqual((one.status_code, one.json()), (200, self.listed(release_id=saved)))
        for bad in ('?release=x', '?release=' + str(uuid.uuid4()), '?force=1', '?release=' + saved + '&force=1'):
            self.assertEqual(self.client.get(url + bad, secure=True, **actor).status_code, 400, bad)
        self.assertEqual(self.client.get(url, secure=True).status_code, 409)
        self.assertEqual(self.client.post(url, data='{}', content_type='application/json', secure=True, **actor).status_code, 405)
        self.assertEqual((self.revision(), ReportRelease.objects.count()), (before, 1))
