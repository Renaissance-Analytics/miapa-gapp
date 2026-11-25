"""Vendors and their payment details: every change of how a vendor is paid is approved on its own, against the details it replaces, and kept."""
from datetime import date
import json
from unittest.mock import patch
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.maipa.approvals import services as approvals
from backend.maipa.approvals.models import ActionRequest
from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.ledger import services as ledger, vendors
from backend.maipa.ledger.models import Journal, Vendor, VendorPayment
from backend.maipa.workspace.models import Installation, Membership
from tests.test_bookkeeping_posting import BookkeepingPostingTests

PERIOD = {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'}
FIRST = 'Synthetic Bank, account 000123456789, routing 110000000'
SECOND = 'Other Synthetic Bank, account 000987654321, routing 220000000'
CALLED = 'Called the number on our 2025 contract and spoke to their accounts team'


class VendorPaymentDetailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.user = get_user_model().objects.create_user(username='vendor-owner')
        cls.other = get_user_model().objects.create_user(username='vendor-other')
        Membership.objects.create(user=cls.user, installation=install, role='preparer')
        Membership.objects.create(user=cls.other, installation=install, role='administrator')
        cls.entity = Entity.objects.create(engagement=Engagement.objects.create(
            owner=cls.user, installation=install, name='Synthetic vendors'), name='Synthetic vendor business')

    def setUp(self):
        self.book = ledger.create_book(self.user, self.entity.pk, PERIOD)
        clock = patch('backend.maipa.ledger.vendors.today', return_value=date(2026, 3, 10)); clock.start(); self.addCleanup(clock.stop)

    def request(self, operation, payload, user=None):
        return approvals.request_action(user or self.user, {'operation': operation, 'summary': 'Synthetic vendor change', 'payload': {'book_id': self.book['id'], **payload}})

    def approve(self, item):
        return approvals.decide(self.user, item['id'], {'decision': 'approve', 'payload_hash': item['payload_hash']})

    def vendor(self, name='Synthetic Paper Supply'):
        done = self.approve(self.request('vendor.add', {'name': name}))
        self.assertEqual(done['state'], 'approved', done)
        return done['result']['vendor_id']

    def pay(self, vendor, details=FIRST, expected_version=0, verified_by=CALLED, method='bank_transfer', user=None):
        return self.request('vendor.payment', {'vendor_id': vendor, 'method': method, 'details': details, 'verified_by': verified_by, 'expected_version': expected_version}, user)

    def listed(self, user=None):
        return vendors.listing(user or self.user, self.book['id'])

    def test_a_vendor_and_its_first_payment_details_are_added_by_approval_and_read_back_masked(self):
        item = self.request('vendor.add', {'name': 'Synthetic Paper Supply'})
        self.assertEqual(item['detail'], {'books': 'Synthetic vendor business', 'name': 'Synthetic Paper Supply', 'current': True})
        self.assertEqual(Vendor.objects.count(), 0)
        journals, revision = Journal.objects.count(), ledger.read_book(self.user, self.book['id'])['revision']
        vendor = self.approve(item)['result']['vendor_id']
        self.assertEqual(self.listed(), {'book_id': self.book['id'], 'vendors': [{'id': vendor, 'name': 'Synthetic Paper Supply', 'payment': None, 'versions': 0}]})
        first = self.pay(vendor)
        self.assertEqual(first['detail'], {'books': 'Synthetic vendor business', 'vendor': 'Synthetic Paper Supply', 'version': 1, 'method': 'bank_transfer', 'details': FIRST,
                                           'verified_by': CALLED, 'previous': None, 'current': True})
        self.assertEqual(VendorPayment.objects.count(), 0)
        self.assertEqual(first['payload']['details'], 'Synthetic Bank, account ••••••••6789, routing •••••0000')     # Lists never echo the numbers.
        told = approvals.request_action(self.user, {'operation': 'vendor.payment', 'summary': 'Pay to account 000123456789, routing 110000000',
            'payload': {'book_id': self.book['id'], 'vendor_id': vendor, 'method': 'check', 'details': 'By check to their office', 'verified_by': CALLED, 'expected_version': 0}})
        self.assertEqual(told['summary'], 'Pay to account ••••••••6789, routing •••••0000')                       # Nor does the agent's own summary.
        approvals.decide(self.user, told['id'], {'decision': 'reject', 'payload_hash': told['payload_hash']})
        done = self.approve(first)
        self.assertNotIn('000123456789', json.dumps(approvals.list_requests(self.user)))
        self.assertEqual((done['state'], done['result']), ('approved', {'vendor_id': vendor, 'version': 1}))
        # Account and routing numbers are masked when read back; the last four digits stay so the person can recognise them.
        self.assertEqual(self.listed()['vendors'][0], {'id': vendor, 'name': 'Synthetic Paper Supply', 'versions': 1, 'payment': {
            'version': 1, 'method': 'bank_transfer', 'details': 'Synthetic Bank, account ••••••••6789, routing •••••0000', 'verified_by': CALLED, 'set_on': '2026-03-10'}})
        # Vendors and their payment details are not ledger entries; approving again changes nothing.
        self.assertEqual((Journal.objects.count(), ledger.read_book(self.user, self.book['id'])['revision'], self.approve(first)['result'], VendorPayment.objects.count()),
                         (journals, revision, done['result'], 1))

    def test_a_change_of_payment_details_shows_what_it_replaces_and_every_version_is_kept(self):
        vendor = self.vendor()
        self.approve(self.pay(vendor))
        with patch('backend.maipa.ledger.vendors.today', return_value=date(2026, 5, 2)):
            change = self.pay(vendor, SECOND, expected_version=1, verified_by='Confirmed in person with their director at the March trade fair')
            self.assertEqual(change['detail'], {'books': 'Synthetic vendor business', 'vendor': 'Synthetic Paper Supply', 'version': 2, 'method': 'bank_transfer', 'details': SECOND,
                'verified_by': 'Confirmed in person with their director at the March trade fair', 'current': True,
                'previous': {'version': 1, 'method': 'bank_transfer', 'details': 'Synthetic Bank, account ••••••••6789, routing •••••0000', 'set_on': '2026-03-10'}})
            self.assertEqual(self.listed()['vendors'][0]['payment']['version'], 1)                    # Unchanged until approved.
            done = self.approve(change)
        self.assertEqual(done['result'], {'vendor_id': vendor, 'version': 2})
        current = self.listed()['vendors'][0]
        self.assertEqual((current['versions'], current['payment']['version'], current['payment']['details'], current['payment']['set_on']),
                         (2, 2, 'Other Synthetic Bank, account ••••••••4321, routing •••••0000', '2026-05-02'))
        history = vendors.history(self.user, self.book['id'], vendor)
        self.assertEqual([(row['version'], row['set_on'], row['details']) for row in history['versions']],
                         [(2, '2026-05-02', 'Other Synthetic Bank, account ••••••••4321, routing •••••0000'), (1, '2026-03-10', 'Synthetic Bank, account ••••••••6789, routing •••••0000')])
        saved = VendorPayment.objects.get(vendor_id=vendor, version=1)
        self.assertEqual(saved.details, FIRST)                                                        # The stored record is exact.
        saved.details = 'Edited'
        with self.assertRaises(ValueError):
            saved.save()
        with self.assertRaises(ValueError):
            saved.delete()

    def test_a_change_prepared_against_older_details_fails_when_they_have_changed_since(self):
        vendor = self.vendor()
        self.approve(self.pay(vendor))
        slow = self.pay(vendor, SECOND, expected_version=1)
        quick = self.pay(vendor, 'Third Synthetic Bank, account 555000111222', expected_version=1)
        self.assertEqual(self.approve(quick)['state'], 'approved')
        late = self.approve(slow)
        self.assertEqual((late['state'], late['error'], VendorPayment.objects.count()), ('failed', 'vendor_payment_changed', 2))
        self.assertEqual(self.listed()['vendors'][0]['payment']['details'], 'Third Synthetic Bank, account ••••••••1222')

    def test_unverified_unchanged_malformed_and_foreign_changes_are_refused_before_anything_is_saved(self):
        vendor = self.vendor()
        for payload, code in (({'name': 'synthetic  paper SUPPLY'}, 'vendor_exists'), ({'name': ''}, None), ({'name': ' padded '}, None), ({'name': 'x' * 201}, None), ({'name': 5}, None),
                              ({'name': 'New vendor', 'force': True}, None)):
            with self.assertRaises(approvals.ApprovalError, msg=payload) as refused:
                self.request('vendor.add', payload)
            if code:
                self.assertEqual(refused.exception.code, code, payload)
        cases = ((dict(verified_by='ok'), 'vendor_verification_required'), (dict(verified_by=''), None), (dict(method='wire fraud'), None), (dict(details=''), None),
                 (dict(details='x' * 501), None), (dict(expected_version=1), 'vendor_payment_changed'), (dict(expected_version='0'), None), (dict(expected_version=True), None),
                 (dict(user=self.other), None))
        for changes, code in cases:
            with self.assertRaises(approvals.ApprovalError, msg=changes) as refused:
                self.pay(vendor, **changes)
            if code:
                self.assertEqual(refused.exception.code, code, changes)
        for target in (str(uuid.uuid4()), 'vendor'):
            with self.assertRaises(approvals.ApprovalError):
                self.pay(target)
        self.assertEqual((ActionRequest.objects.filter(operation='vendor.payment').count(), VendorPayment.objects.count()), (0, 0))
        self.approve(self.pay(vendor))
        with self.assertRaises(approvals.ApprovalError) as refused:
            self.pay(vendor, expected_version=1)
        self.assertEqual(refused.exception.code, 'vendor_payment_unchanged')
        with self.assertRaises(ledger.LedgerError):
            self.listed(self.other)
        with self.assertRaises(ledger.LedgerError):
            vendors.history(self.other, self.book['id'], vendor)
        with self.assertRaises(ledger.LedgerError):
            vendors.history(self.user, self.book['id'], str(uuid.uuid4()))

    def test_the_agent_tool_lists_vendors_masked_and_cannot_change_them(self):
        vendor = self.vendor()
        self.approve(self.pay(vendor))
        broker, _, _ = BookkeepingPostingTests.broker(self)
        tool = lambda arguments: broker.call('maipa_books_vendors', {'book_id': self.book['id'], **arguments})
        self.assertEqual(json.loads(tool({})['content'][0]['text']), self.listed())
        self.assertEqual(json.loads(tool({'vendor_id': vendor})['content'][0]['text']), vendors.history(self.user, self.book['id'], vendor))
        text = tool({})['content'][0]['text'] + tool({'vendor_id': vendor})['content'][0]['text']
        self.assertNotIn('000123456789', text); self.assertNotIn('110000000', text)
        for bad in ({'vendor_id': str(uuid.uuid4())}, {'name': 'New vendor'}, {'vendor_id': vendor, 'details': SECOND}):
            self.assertTrue(tool(bad)['isError'], bad)
        self.assertEqual((Vendor.objects.count(), VendorPayment.objects.count()), (1, 1))
