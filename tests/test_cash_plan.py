"""Planned cash items and scenarios: what the person expects but has not booked, shown beside the books-only forecast and never in the books."""
import json
from unittest.mock import patch

from django.test import TestCase

from backend.maipa.approvals import services as approvals
from backend.maipa.approvals.models import ActionRequest
from backend.maipa.ledger import plans, services as ledger
from backend.maipa.ledger.models import CashPlanItem, Journal
from tests.test_bookkeeping_posting import BookkeepingPostingTests
from tests.test_cash_forecast import CashForecastTests


class CashPlanTests(TestCase):
    setUpTestData = classmethod(CashForecastTests.setUpTestData.__func__)
    revision, lines, entry, restrict, approve, known = (getattr(CashForecastTests, name) for name in ('revision', 'lines', 'entry', 'restrict', 'approve', 'known'))

    def setUp(self):
        CashForecastTests.setUp(self)
        self.approve(self.restrict())
        self.known()

    def ask(self, user=None, **changes):
        payload = {'book_id': self.book['id'], 'description': 'Big client invoice', 'amount': '2000', 'date': '2026-03-18', **changes}
        return approvals.request_action(user or self.user, {'operation': 'plan.add', 'summary': 'Plan the big client receipt', 'payload': payload})

    def planned(self, **changes):
        done = self.approve(self.ask(**changes))
        self.assertEqual(done['state'], 'approved', done)
        return done['result']['item_id']

    def forecast(self, scenario=None, user=None):
        return plans.forecast(user or self.user, self.book['id'], {'from': '2026-03-02', **({'scenario': scenario} if scenario else {})})

    def test_approved_plans_run_beside_the_books_forecast_and_repeat_on_their_dates(self):
        books = ledger.cash_forecast(self.user, self.book['id'], {'from': '2026-03-02'})
        empty = self.forecast()
        self.assertEqual(({key: empty[key] for key in books}, empty['plan'], empty['plan_items'], empty['scenarios']), (books, None, [], []))
        item = self.ask()
        self.assertEqual(item['detail'], {'books': 'Synthetic forecast business', 'currency': 'USD', 'description': 'Big client invoice', 'amount': '2000.0000',
                                          'date': '2026-03-18', 'every': None, 'until': None, 'scenario': None, 'current': True})
        self.assertEqual((CashPlanItem.objects.count(), self.forecast()['plan']), (0, None))            # Nothing is planned until approved.
        journals, revision = Journal.objects.count(), self.revision()
        done = self.approve(item)
        self.assertEqual(done['state'], 'approved')
        receipt = done['result']['item_id']
        payroll = self.planned(description='Payroll', amount='-1200', date='2026-03-27', every='month', until='2026-05-31')
        cleaner = self.planned(description='Cleaner', amount='-100', date='2026-03-02', every='week', until='2026-03-22')
        value = self.forecast()
        # The books-only forecast is exactly what it was; planning changed no entry and no book revision.
        self.assertEqual(({key: value[key] for key in books}, Journal.objects.count(), self.revision()), (books, journals, revision))
        self.assertEqual(value['plan_items'], [
            {'id': cleaner, 'description': 'Cleaner', 'amount': '-100.0000', 'date': '2026-03-02', 'every': 'week', 'until': '2026-03-22', 'scenario': None},
            {'id': receipt, 'description': 'Big client invoice', 'amount': '2000.0000', 'date': '2026-03-18', 'every': None, 'until': None, 'scenario': None},
            {'id': payroll, 'description': 'Payroll', 'amount': '-1200.0000', 'date': '2026-03-27', 'every': 'month', 'until': '2026-05-31', 'scenario': None}])
        plan = value['plan']
        self.assertEqual((plan['scenario'], plan['opening_usable'], plan['closing_usable'], plan['lowest'], len(plan['weeks'])),
                         (None, '4000.0000', '-450.0000', {'week': 13, 'closing': '-450.0000'}, 13))
        weeks = {week['week']: week for week in plan['weeks']}
        self.assertEqual(weeks[3], {'week': 3, 'start': '2026-03-16', 'end': '2026-03-22', 'planned_in': '2000.0000', 'planned_out': '100.0000', 'closing': '4950.0000',
            'items': [{'date': '2026-03-16', 'description': 'Cleaner', 'amount': '-100.0000', 'scenario': None},
                      {'date': '2026-03-18', 'description': 'Big client invoice', 'amount': '2000.0000', 'scenario': None}]})
        self.assertEqual([(n, weeks[n]['closing']) for n in (1, 2, 4, 5, 9, 10, 13)],
                         [(1, '3000.0000'), (2, '2650.0000'), (4, '3750.0000'), (5, '2850.0000'), (9, '1650.0000'), (10, '750.0000'), (13, '-450.0000')])
        self.assertEqual([[i['date'] for i in weeks[n]['items']] for n in (4, 9, 13)], [['2026-03-27'], ['2026-04-27'], ['2026-05-27']])
        self.assertEqual(sum(len(week['items']) for week in plan['weeks']), 7)
        # Plans never reach the statements.
        self.assertEqual(ledger.financial_report(self.user, self.book['id'], {'from': '2026-01-01', 'to': '2026-12-31'})['income_statement']['total_income'], '5400.0000')
        self.assertEqual(self.approve(item)['result'], done['result'])

    def test_a_scenario_is_counted_only_when_asked_for_and_sits_on_top_of_the_plan(self):
        self.planned()
        hire = self.planned(description='Assistant salary', amount='-800', date='2026-04-01', every='month', scenario='Hire an assistant')
        base = self.forecast()
        self.assertEqual((base['plan']['closing_usable'], base['plan']['scenario'], base['scenarios']), ('3450.0000', None, [{'name': 'Hire an assistant', 'items': 1}]))
        self.assertEqual([(i['id'] == hire, i['scenario']) for i in base['plan_items']], [(False, None), (True, 'Hire an assistant')])
        what_if = self.forecast('Hire an assistant')
        self.assertEqual((what_if['plan']['scenario'], what_if['plan']['closing_usable'], what_if['plan']['lowest']), ('Hire an assistant', '1850.0000', {'week': 10, 'closing': '1850.0000'}))
        salary = [(week['week'], item['date'], item['scenario']) for week in what_if['plan']['weeks'] for item in week['items'] if item['scenario']]
        self.assertEqual(salary, [(5, '2026-04-01', 'Hire an assistant'), (9, '2026-05-01', 'Hire an assistant')])
        with self.assertRaises(ledger.LedgerError) as refused:
            self.forecast('Open a second shop')
        self.assertEqual(refused.exception.code, 'plan_scenario_not_found')

    def test_a_planned_item_is_removed_only_by_approval(self):
        item = self.planned()
        stop = approvals.request_action(self.user, {'operation': 'plan.remove', 'summary': 'The invoice was cancelled', 'payload': {'book_id': self.book['id'], 'item_id': item}})
        self.assertEqual(stop['detail'], {'books': 'Synthetic forecast business', 'currency': 'USD', 'description': 'Big client invoice', 'amount': '2000.0000',
                                          'date': '2026-03-18', 'every': None, 'until': None, 'scenario': None, 'current': True})
        self.assertEqual(self.forecast()['plan']['closing_usable'], '3450.0000')                         # Still planned until approved.
        done = self.approve(stop)
        self.assertEqual((done['state'], done['result']), ('approved', {'item_id': item, 'removed': True}))
        value = self.forecast()
        self.assertEqual((value['plan'], value['plan_items']), (None, []))
        with self.assertRaises(approvals.ApprovalError) as refused:
            approvals.request_action(self.user, {'operation': 'plan.remove', 'summary': 'Remove it again', 'payload': {'book_id': self.book['id'], 'item_id': item}})
        self.assertEqual(refused.exception.code, 'plan_item_not_found')

    def test_malformed_plans_other_people_and_too_many_items_are_refused_before_anything_is_saved(self):
        cases = ((dict(amount='0'), None), (dict(amount='2,000'), None), (dict(amount=2000), None), (dict(date='2025-12-31'), 'ledger_date_outside_period'),
                 (dict(date='2027-01-01'), 'ledger_date_outside_period'), (dict(date='soon'), None), (dict(every='day'), None), (dict(until='2026-06-30'), 'plan_until_needs_repeat'),
                 (dict(every='month', until='2026-03-17'), 'plan_until_before_date'), (dict(description=''), None), (dict(description='x' * 201), None),
                 (dict(scenario=''), None), (dict(scenario=' padded '), None), (dict(scenario='x' * 61), None), (dict(force=True), None))
        for changes, code in cases:
            with self.assertRaises(approvals.ApprovalError, msg=changes) as refused:
                self.ask(**changes)
            if code:
                self.assertEqual(refused.exception.code, code, changes)
        with self.assertRaises(approvals.ApprovalError):
            self.ask(self.other)
        with self.assertRaises(ledger.LedgerError):
            self.forecast(user=self.other)
        for bad in ({'from': '2026-03-02', 'scenario': ''}, {'from': '2026-03-02', 'scenario': 5}, {'from': '2026-03-02', 'plan': False}, {}):
            with self.assertRaises(ledger.LedgerError, msg=bad):
                plans.forecast(self.user, self.book['id'], bad)
        self.assertEqual((ActionRequest.objects.filter(operation='plan.add').count(), CashPlanItem.objects.count()), (0, 0))
        waiting = self.ask(description='Second')
        self.planned()
        with patch('backend.maipa.ledger.plans.MAX_ITEMS', 1):
            with self.assertRaises(approvals.ApprovalError) as refused:
                self.ask(description='Third')
            self.assertEqual(refused.exception.code, 'plan_limit')
            done = self.approve(waiting)
        self.assertEqual((done['state'], done['error'], CashPlanItem.objects.count()), ('failed', 'plan_limit', 1))

    def test_the_agent_tool_returns_the_plan_and_a_scenario_and_cannot_change_a_plan(self):
        self.planned()
        self.planned(description='Assistant salary', amount='-800', date='2026-04-01', every='month', scenario='Hire an assistant')
        broker, _, _ = BookkeepingPostingTests.broker(self)
        tool = lambda arguments: broker.call('maipa_books_forecast', {'book_id': self.book['id'], **arguments})
        self.assertEqual(json.loads(tool({'from': '2026-03-02'})['content'][0]['text']), self.forecast())
        self.assertEqual(json.loads(tool({'from': '2026-03-02', 'scenario': 'Hire an assistant'})['content'][0]['text']), self.forecast('Hire an assistant'))
        for bad in ({'from': '2026-03-02', 'scenario': 'Unknown'}, {'from': '2026-03-02', 'description': 'New plan', 'amount': '5'}, {'scenario': 'Hire an assistant'}):
            self.assertTrue(tool(bad)['isError'], bad)
        self.assertEqual(CashPlanItem.objects.count(), 2)
