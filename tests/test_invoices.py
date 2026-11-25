"""Customers and invoices: what customers owe the business, recorded and voided by approval, priced exactly, numbered once, and tied to the ledger."""
import json
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.maipa.approvals import services as approvals
from backend.maipa.approvals.models import ActionRequest
from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.ledger import receivables, services as ledger
from backend.maipa.ledger.models import Customer, Invoice, Journal
from backend.maipa.workspace.models import Installation, Membership
from tests.test_bookkeeping_posting import BookkeepingPostingTests

PERIOD = {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'}


class InvoiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='invoice-owner')
        cls.other = get_user_model().objects.create_user(username='invoice-other')
        Membership.objects.create(user=cls.user, installation=install, role='preparer')
        Membership.objects.create(user=cls.other, installation=install, role='administrator')
        cls.entity = Entity.objects.create(engagement=Engagement.objects.create(
            owner=cls.user, installation=install, name='Synthetic invoices'), name='Synthetic invoice business')

    def setUp(self):
        self.book = ledger.create_book(self.user, self.entity.pk, PERIOD)
        self.accounts = {a['code']: a['id'] for a in self.book['accounts']}
        self.harbor = self.approve(self.request('customer.add', {'name': 'Synthetic Harbor Labs'}))['result']['customer_id']
        self.orchard = receivables.add(self.user, self.book['id'], {'name': 'Synthetic Orchard Co'})['customer_id']

    def revision(self):
        return ledger.read_book(self.user, self.book['id'])['revision']

    def request(self, operation, payload, user=None):
        return approvals.request_action(user or self.user, {'operation': operation, 'summary': 'Synthetic invoice change', 'payload': {'book_id': self.book['id'], **payload}})

    def approve(self, item):
        return approvals.decide(self.user, item['id'], {'decision': 'approve', 'payload_hash': item['payload_hash']})

    def line(self, **changes):
        return {'description': 'Design work', 'quantity': '7.5', 'unit_price': '135', 'account_id': self.accounts['4000'], **changes}

    def invoice(self, user=None, **changes):
        payload = {'customer_id': self.harbor, 'number': 'INV-0042', 'invoice_date': '2026-03-02', 'due_date': '2026-04-01',
                   'lines': [self.line()], 'receivable_account_id': self.accounts['1100'], **changes}
        return self.request('invoice.record', payload, user)

    def recorded(self, **changes):
        done = self.approve(self.invoice(**changes))
        self.assertEqual(done['state'], 'approved', done)
        return done['result']['invoice_id']

    def listed(self, as_of='2026-03-20', user=None):
        return receivables.receivables(user or self.user, self.book['id'], {'as_of': as_of})

    def test_a_customer_and_an_invoice_are_recorded_by_approval_and_the_receivables_tie_to_the_ledger(self):
        self.assertEqual([c.name for c in Customer.objects.order_by('name')], ['Synthetic Harbor Labs', 'Synthetic Orchard Co'])
        with self.assertRaises(approvals.ApprovalError) as refused:
            self.request('customer.add', {'name': 'synthetic  harbor labs'})
        self.assertEqual(refused.exception.code, 'customer_exists')
        self.assertEqual(self.listed()['next_number'], None)
        item = self.invoice(lines=[self.line(), self.line(description='Hosting, March', quantity='1', unit_price='49.99')])
        self.assertEqual(item['detail'], {'books': 'Synthetic invoice business', 'currency': 'USD', 'customer': 'Synthetic Harbor Labs', 'number': 'INV-0042',
            'invoice_date': '2026-03-02', 'due_date': '2026-04-01', 'total': '1062.4900', 'receivable': '1100 Accounts receivable', 'current': True,
            'lines': [{'description': 'Design work', 'quantity': '7.5000', 'unit_price': '135.0000', 'amount': '1012.5000', 'account': '4000 Sales'},
                      {'description': 'Hosting, March', 'quantity': '1.0000', 'unit_price': '49.9900', 'amount': '49.9900', 'account': '4000 Sales'}]})
        self.assertEqual((Invoice.objects.count(), Journal.objects.count()), (0, 0))
        before = self.revision()
        done = self.approve(item)
        self.assertEqual(done['state'], 'approved')
        invoice = Invoice.objects.get(pk=done['result']['invoice_id'])
        entry = invoice.journal
        self.assertEqual((invoice.state, entry.state, entry.source_kind, entry.date.isoformat(), entry.description, self.revision()),
                         ('open', 'posted', 'invoice', '2026-03-02', 'Invoice INV-0042 to Synthetic Harbor Labs', before + 2))
        self.assertEqual([(line.account.code, line.debit_units, line.credit_units) for line in entry.lines.order_by('position')],
                         [('1100', 10624900, 0), ('4000', 0, 10125000), ('4000', 0, 499900)])
        self.assertEqual(self.listed(), {'book_id': self.book['id'], 'currency': 'USD', 'as_of': '2026-03-20', 'open_total': '1062.4900', 'overdue_total': '0.0000',
            'ledger_receivable': '1062.4900', 'difference': '0.0000', 'next_number': 'INV-0043', 'recent': [],
            'customers': [{'id': self.harbor, 'name': 'Synthetic Harbor Labs'}, {'id': self.orchard, 'name': 'Synthetic Orchard Co'}],
            'invoices': [{'id': str(invoice.pk), 'customer': 'Synthetic Harbor Labs', 'number': 'INV-0042', 'invoice_date': '2026-03-02', 'due_date': '2026-04-01',
                          'total': '1062.4900', 'state': 'open', 'days_overdue': 0, 'lines': 2}]})
        report = ledger.financial_report(self.user, self.book['id'], {'from': '2026-01-01', 'to': '2026-03-31'})
        self.assertEqual(report['income_statement']['total_income'], '1062.4900')
        self.assertEqual(ledger.verify_book(self.user, self.book['id'])['findings'], [])
        self.assertEqual((self.approve(item)['result'], Journal.objects.count()), (done['result'], 1))      # Approving again changes nothing.

    def test_each_line_is_quantity_times_price_rounded_half_up_to_the_cent(self):
        self.assertEqual([receivables.line_amount(q, p) for q, p in ((30000, 333350), (30000, 333330), (10000, 50), (1, 1))], [1000100, 1000000, 100, 0])
        priced = self.invoice(lines=[self.line(quantity='3', unit_price='33.335'), self.line(quantity='2.5', unit_price='0.01'), self.line(quantity='0.3333', unit_price='3')])
        self.assertEqual([(line['amount']) for line in priced['detail']['lines']], ['100.0100', '0.0300', '1.0000'])
        self.assertEqual(priced['detail']['total'], '101.0400')
        with self.assertRaises(approvals.ApprovalError) as refused:
            self.invoice(lines=[self.line(quantity='0.0001', unit_price='0.01')])
        self.assertEqual(refused.exception.code, 'invoice_line_zero')

    def test_a_number_is_used_once_even_after_a_void_and_an_overdue_invoice_comes_first(self):
        late = self.recorded(number='INV-0007', invoice_date='2026-02-01', due_date='2026-03-03', lines=[self.line(quantity='1', unit_price='300')])
        soon = self.recorded(customer_id=self.orchard)
        for number in ('inv-0042', 'INV 0042', 'Inv0042'):
            with self.assertRaises(approvals.ApprovalError, msg=number) as refused:
                self.invoice(number=number, customer_id=self.orchard)
            self.assertEqual(refused.exception.code, 'invoice_duplicate_number', number)
        listed = self.listed('2026-03-20')
        self.assertEqual([(i['id'], i['state'], i['days_overdue']) for i in listed['invoices']], [(late, 'overdue', 17), (soon, 'open', 0)])
        self.assertEqual((listed['open_total'], listed['overdue_total'], listed['next_number']), ('1312.5000', '300.0000', 'INV-0043'))
        stop = self.request('invoice.void', {'invoice_id': late, 'date': '2026-03-21'})
        self.assertEqual(stop['detail'], {'books': 'Synthetic invoice business', 'currency': 'USD', 'customer': 'Synthetic Harbor Labs', 'number': 'INV-0007',
                                          'total': '300.0000', 'invoice_date': '2026-02-01', 'date': '2026-03-21', 'current': True})
        self.assertEqual(self.approve(stop)['state'], 'approved')
        invoice = Invoice.objects.get(pk=late)
        self.assertEqual((invoice.state, invoice.void_journal.source_kind, invoice.void_journal.source_reference), ('void', 'reversal', str(invoice.journal_id)))
        after = self.listed('2026-03-22')
        self.assertEqual((after['open_total'], after['ledger_receivable'], [(r['number'], r['state']) for r in after['recent']]), ('1012.5000', '1012.5000', [('INV-0007', 'void')]))
        with self.assertRaises(approvals.ApprovalError) as refused:
            self.request('invoice.void', {'invoice_id': late, 'date': '2026-03-22'})
        self.assertEqual(refused.exception.code, 'invoice_not_open')
        with self.assertRaises(approvals.ApprovalError) as refused:
            self.invoice(number='INV-0007', invoice_date='2026-03-22', due_date='2026-04-21')
        self.assertEqual(refused.exception.code, 'invoice_duplicate_number')                               # A voided number stays used.
        # A manual entry on the receivable account shows up as a difference, not hidden.
        made = ledger.propose_journal(self.user, self.book['id'], {'operation_id': str(uuid.uuid4()), 'expected_revision': self.revision(), 'date': '2026-03-23',
            'description': 'Manual receivable', 'source': {'kind': 'manual', 'reference': 'synthetic-manual'}, 'evidence': ['synthetic'],
            'lines': [{'account_id': self.accounts['1100'], 'debit': '40', 'credit': '0'}, {'account_id': self.accounts['4000'], 'debit': '0', 'credit': '40'}]})
        ledger.confirm_own_journal(self.user, self.book['id'], made['id'], {'expected_revision': self.revision(), 'payload_hash': made['payload_hash'], 'confirm': True})
        final = self.listed('2026-03-24')
        self.assertEqual((final['open_total'], final['ledger_receivable'], final['difference']), ('1012.5000', '1052.5000', '40.0000'))

    def test_malformed_invoices_wrong_accounts_closed_months_and_other_people_are_refused_and_nothing_is_posted(self):
        cases = ((dict(customer_id=str(uuid.uuid4())), 'customer_not_found'), (dict(number=''), None), (dict(number='x' * 41), None), (dict(number='--'), 'invoice_number_invalid'),
                 (dict(due_date='2026-03-01'), 'invoice_due_before_date'), (dict(invoice_date='2025-12-31'), 'ledger_date_outside_period'), (dict(invoice_date='soon'), None),
                 (dict(receivable_account_id=self.accounts['1000']), 'invoice_receivable_invalid'), (dict(receivable_account_id=self.accounts['2000']), 'invoice_receivable_invalid'),
                 (dict(lines=[]), None), (dict(lines=[self.line()] * 51), None), (dict(lines=[self.line(account_id=self.accounts['6200'])]), 'invoice_account_invalid'),
                 (dict(lines=[self.line(account_id=self.accounts['1100'])]), 'invoice_account_invalid'), (dict(lines=[self.line(quantity='0')]), None),
                 (dict(lines=[self.line(unit_price='-1')]), None), (dict(lines=[self.line(quantity=2)]), None), (dict(lines=[{**self.line(), 'tax': '1'}]), None),
                 (dict(lines=[self.line(description='')]), None), (dict(force=True), None))
        for changes, code in cases:
            with self.assertRaises(approvals.ApprovalError, msg=changes) as refused:
                self.invoice(**changes)
            if code:
                self.assertEqual(refused.exception.code, code, changes)
        with self.assertRaises(approvals.ApprovalError):
            self.invoice(self.other)
        self.assertEqual((ActionRequest.objects.filter(operation='invoice.record').count(), Invoice.objects.count(), Journal.objects.count()), (0, 0, 0))
        invoice = self.recorded()
        for payload in ({'invoice_id': invoice, 'date': '2026-03-01'}, {'invoice_id': str(uuid.uuid4()), 'date': '2026-03-05'}, {'invoice_id': invoice}):
            with self.assertRaises(approvals.ApprovalError, msg=payload):
                self.request('invoice.void', payload)
        with self.assertRaises(approvals.ApprovalError):
            self.request('invoice.void', {'invoice_id': invoice, 'date': '2026-03-05'}, self.other)
        for bad in ({}, {'as_of': 'today'}, {'as_of': '2026-03-20', 'customer': 'x'}):
            with self.assertRaises(ledger.LedgerError, msg=bad):
                receivables.receivables(self.user, self.book['id'], bad)
        with self.assertRaises(ledger.LedgerError):
            self.listed(user=self.other)
        # A month closed after the request makes the approval fail with no invoice and no entry.
        waiting = self.invoice(number='INV-0050', invoice_date='2026-03-15', due_date='2026-04-14')
        ledger.change_period(self.user, self.book['id'], {'expected_revision': self.revision(), 'action': 'close', 'through': '2026-03-31'})
        journals = Journal.objects.count()
        late = self.approve(waiting)
        self.assertEqual((late['state'], late['error']), ('failed', 'ledger_period_closed'))
        self.assertEqual((Invoice.objects.count(), Journal.objects.count()), (1, journals))

    def test_next_number_keeps_the_prefix_and_width_of_the_latest_number(self):
        self.assertEqual([receivables.next_number(numbers) for numbers in (['INV-0099'], ['2026-17'], ['A9'], ['Spring', 'INV-12'], ['Spring'], [])],
                         ['INV-0100', '2026-18', 'A10', 'INV-13', None, None])

    def test_the_agent_tool_lists_receivables_and_cannot_record_or_void(self):
        invoice = self.recorded()
        broker, _, _ = BookkeepingPostingTests.broker(self)
        tool = lambda arguments: broker.call('maipa_books_receivables', {'book_id': self.book['id'], **arguments})
        self.assertEqual(json.loads(tool({'as_of': '2026-03-20'})['content'][0]['text']), self.listed())
        for bad in ({}, {'as_of': 'today'}, {'as_of': '2026-03-20', 'invoice_id': invoice, 'date': '2026-03-21'}, {'as_of': '2026-03-20', 'number': 'INV-9'}):
            self.assertTrue(tool(bad)['isError'], bad)
        self.assertEqual((Invoice.objects.count(), Invoice.objects.get(pk=invoice).state), (1, 'open'))
