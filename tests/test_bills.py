"""Bills: what the business owes its vendors, recorded, paid and voided by approval, never entered twice unnoticed, and tied to the ledger."""
import json
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.maipa.approvals import services as approvals
from backend.maipa.approvals.models import ActionRequest
from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.ledger import bills, services as ledger, vendors
from backend.maipa.ledger.models import Bill, Journal
from backend.maipa.workspace.models import Installation, Membership
from tests.test_bookkeeping_posting import BookkeepingPostingTests

PERIOD = {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'}


class BillTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='bill-owner')
        cls.other = get_user_model().objects.create_user(username='bill-other')
        Membership.objects.create(user=cls.user, installation=install, role='preparer')
        Membership.objects.create(user=cls.other, installation=install, role='administrator')
        cls.entity = Entity.objects.create(engagement=Engagement.objects.create(
            owner=cls.user, installation=install, name='Synthetic bills'), name='Synthetic bill business')

    def setUp(self):
        self.book = ledger.create_book(self.user, self.entity.pk, PERIOD)
        self.accounts = {a['code']: a['id'] for a in self.book['accounts']}
        self.paper = vendors.add(self.user, self.book['id'], {'name': 'Synthetic Paper Supply'})['vendor_id']
        self.power = vendors.add(self.user, self.book['id'], {'name': 'Synthetic Power Company'})['vendor_id']
        made = ledger.propose_journal(self.user, self.book['id'], {
            'operation_id': str(uuid.uuid4()), 'expected_revision': 0, 'date': '2026-02-10', 'description': 'Client payment',
            'source': {'kind': 'manual', 'reference': 'synthetic-sale'}, 'evidence': ['synthetic'],
            'lines': [{'account_id': self.accounts['1000'], 'debit': '5000', 'credit': '0'}, {'account_id': self.accounts['4000'], 'debit': '0', 'credit': '5000'}]})
        ledger.confirm_own_journal(self.user, self.book['id'], made['id'], {'expected_revision': 1, 'payload_hash': made['payload_hash'], 'confirm': True})

    def revision(self):
        return ledger.read_book(self.user, self.book['id'])['revision']

    def request(self, operation, payload, user=None):
        return approvals.request_action(user or self.user, {'operation': operation, 'summary': 'Synthetic bill change', 'payload': {'book_id': self.book['id'], **payload}})

    def approve(self, item):
        return approvals.decide(self.user, item['id'], {'decision': 'approve', 'payload_hash': item['payload_hash']})

    def bill(self, user=None, **changes):
        payload = {'vendor_id': self.paper, 'reference': 'INV-1042', 'bill_date': '2026-03-03', 'due_date': '2026-04-02', 'amount': '480.50',
                   'description': 'March paper order', 'account_id': self.accounts['6200'], 'payable_account_id': self.accounts['2000'], **changes}
        return self.request('bill.record', payload, user)

    def recorded(self, **changes):
        done = self.approve(self.bill(**changes))
        self.assertEqual(done['state'], 'approved', done)
        return done['result']['bill_id']

    def pay(self, bill, date='2026-03-28', cash='1000', user=None):
        return self.request('bill.pay', {'bill_id': bill, 'date': date, 'cash_account_id': self.accounts[cash]}, user)

    def payables(self, as_of='2026-03-20', user=None):
        return bills.payables(user or self.user, self.book['id'], {'as_of': as_of})

    def test_a_bill_is_recorded_and_paid_by_approval_and_the_payables_tie_to_the_ledger(self):
        item = self.bill()
        self.assertEqual(item['detail'], {'books': 'Synthetic bill business', 'currency': 'USD', 'vendor': 'Synthetic Paper Supply', 'reference': 'INV-1042',
            'bill_date': '2026-03-03', 'due_date': '2026-04-02', 'amount': '480.5000', 'description': 'March paper order', 'account': '6200 Office expenses',
            'payable': '2000 Accounts payable', 'possible_duplicates': [], 'current': True})
        self.assertEqual((Bill.objects.count(), Journal.objects.count()), (0, 1))
        before = self.revision()
        done = self.approve(item)
        self.assertEqual(done['state'], 'approved')
        bill = Bill.objects.get(pk=done['result']['bill_id'])
        entry = bill.journal
        self.assertEqual((bill.state, entry.state, entry.source_kind, entry.date.isoformat(), entry.description, self.revision()),
                         ('open', 'posted', 'bill', '2026-03-03', 'Synthetic Paper Supply INV-1042: March paper order', before + 2))
        self.assertEqual([(line.account.code, line.debit_units, line.credit_units) for line in entry.lines.order_by('position')], [('6200', 4805000, 0), ('2000', 0, 4805000)])
        listed = self.payables()
        self.assertEqual(listed, {'book_id': self.book['id'], 'currency': 'USD', 'as_of': '2026-03-20', 'open_total': '480.5000', 'overdue_total': '0.0000',
            'ledger_payable': '480.5000', 'difference': '0.0000', 'recent': [],
            'bills': [{'id': str(bill.pk), 'vendor': 'Synthetic Paper Supply', 'reference': 'INV-1042', 'description': 'March paper order', 'bill_date': '2026-03-03',
                       'due_date': '2026-04-02', 'amount': '480.5000', 'state': 'open', 'days_overdue': 0}]})
        paying = self.pay(str(bill.pk))
        self.assertEqual(paying['detail'], {'books': 'Synthetic bill business', 'currency': 'USD', 'vendor': 'Synthetic Paper Supply', 'reference': 'INV-1042', 'amount': '480.5000',
            'date': '2026-03-28', 'cash_account': '1000 Checking', 'payable': '2000 Accounts payable', 'due_date': '2026-04-02', 'current': True})
        self.assertEqual(self.payables()['open_total'], '480.5000')                                  # Still owed until approved.
        paid = self.approve(paying)
        self.assertEqual(paid['state'], 'approved')
        bill.refresh_from_db()
        payment = bill.payment_journal
        self.assertEqual((bill.state, bill.paid_on.isoformat(), payment.state, payment.source_kind), ('paid', '2026-03-28', 'posted', 'billpay'))
        self.assertEqual([(line.account.code, line.debit_units, line.credit_units) for line in payment.lines.order_by('position')], [('2000', 4805000, 0), ('1000', 0, 4805000)])
        after = self.payables('2026-03-31')
        self.assertEqual((after['bills'], after['open_total'], after['ledger_payable'], [(r['reference'], r['state'], r['paid_on']) for r in after['recent']]),
                         ([], '0.0000', '0.0000', [('INV-1042', 'paid', '2026-03-28')]))
        report = ledger.financial_report(self.user, self.book['id'], {'from': '2026-01-01', 'to': '2026-03-31'})
        self.assertEqual((report['cash_flow']['closing_cash'], report['income_statement']['total_expenses']), ('4519.5000', '480.5000'))
        self.assertEqual(ledger.verify_book(self.user, self.book['id'])['findings'], [])
        # Approving either again changes nothing, and a paid bill cannot be paid twice.
        self.assertEqual((self.approve(item)['result'], self.approve(paying)['result'], Journal.objects.count()), (done['result'], paid['result'], 3))
        with self.assertRaises(approvals.ApprovalError) as refused:
            self.pay(str(bill.pk))
        self.assertEqual(refused.exception.code, 'bill_not_open')

    def test_the_same_bill_is_not_entered_twice_and_a_look_alike_is_pointed_out(self):
        first = self.recorded()
        with self.assertRaises(approvals.ApprovalError) as refused:
            self.bill(reference=' inv-1042 ', amount='99')
        self.assertEqual(refused.exception.code, 'invalid_ledger_request')                              # Padded text is refused outright.
        for reference in ('inv-1042', 'INV 1042', 'Inv-1042'):
            with self.assertRaises(approvals.ApprovalError, msg=reference) as refused:
                self.bill(reference=reference, amount='99')
            self.assertEqual(refused.exception.code, 'bill_duplicate_reference', reference)
        # Another vendor may use the same number.
        self.assertEqual(self.bill(vendor_id=self.power, reference='INV-1042')['detail']['possible_duplicates'], [])
        # Same vendor, same amount, a week later, different number: allowed, and the card shows the earlier bill.
        look_alike = self.bill(reference='INV-1042-B', bill_date='2026-03-10', due_date='2026-04-09')
        self.assertEqual(look_alike['detail']['possible_duplicates'], [{'reference': 'INV-1042', 'bill_date': '2026-03-03', 'amount': '480.5000', 'state': 'open'}])
        self.assertEqual(self.approve(look_alike)['state'], 'approved')
        # Same amount two months later is an ordinary repeat and is not flagged.
        self.assertEqual(self.bill(reference='INV-1101', bill_date='2026-05-04', due_date='2026-06-03')['detail']['possible_duplicates'], [])
        self.assertEqual((Bill.objects.count(), first is not None), (2, True))

    def test_overdue_bills_come_first_and_a_wrong_bill_is_voided_by_reversing_its_entry(self):
        late = self.recorded(reference='INV-0900', bill_date='2026-02-15', due_date='2026-03-01', amount='120')
        soon = self.recorded()
        listed = self.payables('2026-03-20')
        self.assertEqual([(b['id'], b['state'], b['days_overdue']) for b in listed['bills']], [(late, 'overdue', 19), (soon, 'open', 0)])
        self.assertEqual((listed['open_total'], listed['overdue_total'], listed['difference']), ('600.5000', '120.0000', '0.0000'))
        stop = self.request('bill.void', {'bill_id': late, 'date': '2026-03-21'})
        self.assertEqual(stop['detail'], {'books': 'Synthetic bill business', 'currency': 'USD', 'vendor': 'Synthetic Paper Supply', 'reference': 'INV-0900', 'amount': '120.0000',
                                          'bill_date': '2026-02-15', 'date': '2026-03-21', 'current': True})
        done = self.approve(stop)
        self.assertEqual(done['state'], 'approved')
        bill = Bill.objects.get(pk=late)
        self.assertEqual((bill.state, bill.void_journal.source_kind, bill.void_journal.source_reference, bill.void_journal.date.isoformat()),
                         ('void', 'reversal', str(bill.journal_id), '2026-03-21'))
        after = self.payables('2026-03-22')
        self.assertEqual((after['open_total'], after['overdue_total'], after['ledger_payable'], [(r['reference'], r['state']) for r in after['recent']]),
                         ('480.5000', '0.0000', '480.5000', [('INV-0900', 'void')]))
        for call in (lambda: self.pay(late), lambda: self.request('bill.void', {'bill_id': late, 'date': '2026-03-22'})):
            with self.assertRaises(approvals.ApprovalError) as refused:
                call()
            self.assertEqual(refused.exception.code, 'bill_not_open')
        self.assertEqual(self.approve(self.bill(reference='INV-0900', bill_date='2026-03-22', due_date='2026-04-21', amount='125'))['state'], 'approved')   # The number is free again.
        # A manual entry on the payable account shows up as a difference, not hidden.
        made = ledger.propose_journal(self.user, self.book['id'], {'operation_id': str(uuid.uuid4()), 'expected_revision': self.revision(), 'date': '2026-03-23',
            'description': 'Manual payable', 'source': {'kind': 'manual', 'reference': 'synthetic-manual'}, 'evidence': ['synthetic'],
            'lines': [{'account_id': self.accounts['6200'], 'debit': '40', 'credit': '0'}, {'account_id': self.accounts['2000'], 'debit': '0', 'credit': '40'}]})
        ledger.confirm_own_journal(self.user, self.book['id'], made['id'], {'expected_revision': self.revision(), 'payload_hash': made['payload_hash'], 'confirm': True})
        final = self.payables('2026-03-24')
        self.assertEqual((final['open_total'], final['ledger_payable'], final['difference']), ('605.5000', '645.5000', '40.0000'))

    def test_malformed_bills_wrong_accounts_closed_months_and_other_people_are_refused_and_nothing_is_posted(self):
        other_book_vendor = str(uuid.uuid4())
        cases = ((dict(vendor_id=other_book_vendor), 'vendor_not_found'), (dict(amount='0'), None), (dict(amount='-5'), None), (dict(amount=480), None),
                 (dict(due_date='2026-03-02'), 'bill_due_before_date'), (dict(bill_date='2025-12-31'), 'ledger_date_outside_period'), (dict(bill_date='soon'), None),
                 (dict(account_id=self.accounts['4000']), 'bill_account_invalid'), (dict(account_id=self.accounts['2000']), 'bill_account_invalid'),
                 (dict(payable_account_id=self.accounts['1000']), 'bill_payable_invalid'), (dict(payable_account_id=str(uuid.uuid4())), 'ledger_account_not_found'),
                 (dict(reference=''), None), (dict(reference='x' * 101), None), (dict(description=''), None), (dict(force=True), None))
        for changes, code in cases:
            with self.assertRaises(approvals.ApprovalError, msg=changes) as refused:
                self.bill(**changes)
            if code:
                self.assertEqual(refused.exception.code, code, changes)
        with self.assertRaises(approvals.ApprovalError):
            self.bill(self.other)
        self.assertEqual((ActionRequest.objects.count(), Bill.objects.count(), Journal.objects.count()), (0, 0, 1))
        bill = self.recorded()
        for changes, code in ((dict(date='2026-03-02'), 'bill_payment_before_bill'), (dict(cash='6200'), 'bill_cash_account_invalid'), (dict(cash='2000'), 'bill_cash_account_invalid'),
                              (dict(date='2027-01-05'), 'ledger_date_outside_period'), (dict(user=self.other), None)):
            with self.assertRaises(approvals.ApprovalError, msg=changes) as refused:
                self.pay(bill, **changes)
            if code:
                self.assertEqual(refused.exception.code, code, changes)
        with self.assertRaises(approvals.ApprovalError):
            self.pay(str(uuid.uuid4()))
        for bad in ({}, {'as_of': 'today'}, {'as_of': '2026-03-20', 'vendor': 'x'}):
            with self.assertRaises(ledger.LedgerError, msg=bad):
                bills.payables(self.user, self.book['id'], bad)
        with self.assertRaises(ledger.LedgerError):
            self.payables(user=self.other)
        # A month closed after the request makes the approval fail with no bill and no entry.
        waiting = self.bill(reference='INV-2000', bill_date='2026-03-15', due_date='2026-04-14')
        paying = self.pay(bill, date='2026-03-20')
        ledger.change_period(self.user, self.book['id'], {'expected_revision': self.revision(), 'action': 'close', 'through': '2026-03-31'})
        journals = Journal.objects.count()
        late, unpaid = self.approve(waiting), self.approve(paying)
        self.assertEqual((late['state'], late['error'], unpaid['state'], unpaid['error']), ('failed', 'ledger_period_closed', 'failed', 'ledger_period_closed'))
        self.assertEqual((Bill.objects.count(), Bill.objects.get(pk=bill).state, Journal.objects.count()), (1, 'open', journals))

    def test_the_agent_tool_lists_payables_and_cannot_record_or_pay(self):
        bill = self.recorded()
        broker, _, _ = BookkeepingPostingTests.broker(self)
        tool = lambda arguments: broker.call('maipa_books_payables', {'book_id': self.book['id'], **arguments})
        self.assertEqual(json.loads(tool({'as_of': '2026-03-20'})['content'][0]['text']), self.payables())
        for bad in ({}, {'as_of': 'today'}, {'as_of': '2026-03-20', 'bill_id': bill, 'date': '2026-03-21'}, {'as_of': '2026-03-20', 'reference': 'INV-9'}):
            self.assertTrue(tool(bad)['isError'], bad)
        self.assertEqual((Bill.objects.count(), Bill.objects.get(pk=bill).state), (1, 'open'))

    def test_the_panel_endpoint_reads_bills_and_claims_for_the_owner_only_and_changes_nothing(self):
        from backend.maipa.ledger import claims
        self.recorded()
        self.client.force_login(self.user)
        actor = {'HTTP_X_MAIPA_AGENT_ACTOR': str(self.user.pk)}
        url = f"/api/books/{self.book['id']}/payables/"
        revision = self.revision()
        answer = self.client.get(url + '?as_of=2026-03-20', secure=True, **actor)
        self.assertEqual((answer.status_code, answer.json()), (200, {'bills': self.payables(), 'claims': claims.outstanding(self.user, self.book['id'])}))
        for bad in ('', '?as_of=today', '?as_of=2026-03-20&vendor=x'):
            self.assertEqual(self.client.get(url + bad, secure=True, **actor).status_code, 400, bad)
        self.assertEqual(self.client.get(url + '?as_of=2026-03-20', secure=True).status_code, 409)
        self.assertEqual(self.client.post(url + '?as_of=2026-03-20', data='{}', content_type='application/json', secure=True, **actor).status_code, 405)
        self.assertEqual((self.revision(), Bill.objects.count()), (revision, 1))
