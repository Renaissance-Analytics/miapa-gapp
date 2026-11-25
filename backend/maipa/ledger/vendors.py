"""Vendors and their payment details.

A change of how a vendor is paid is a common fraud: someone posing as the vendor asks for payments to go to a new
account. Here every such change is its own approval, prepared against the exact version it replaces, with a
statement of how it was verified, and every version is kept. Details are stored exactly and read back with long
digit runs masked, so account numbers do not travel to the agent or its provider once saved.
"""
from datetime import datetime
import re
import uuid

from django.db import transaction

from .models import Vendor, VendorPayment
from .services import fields, guarded, owned, require, text

METHODS = ('bank_transfer', 'check', 'card', 'cash', 'other')
MAX_VENDORS = 500


def today():
    """Today on the machine Maipa runs on (the site clock is UTC)."""
    return datetime.now().date()


def masked(details):
    """Runs of five or more digits keep only their last four."""
    return re.sub(r'[0-9]{5,}', lambda found: '•' * (len(found.group()) - 4) + found.group()[-4:], details)


def _key(name):
    return ' '.join(name.split()).casefold()


def add_check(book, value):
    fields(value, ('name',))
    name = text(value['name'], 200)
    require(_key(name) not in {_key(existing) for existing in book.vendors.values_list('name', flat=True)}, 'vendor_exists')
    require(book.vendors.count() < MAX_VENDORS, 'vendor_limit')
    return name


def owner_book(user, book_id):
    user, book = owned(user, book_id, True)
    require(book.created_by_id == user.pk, 'ledger_confirmation_denied')
    return user, book


@guarded
@transaction.atomic
def add(user, book_id, value):
    user, book = owner_book(user, book_id)
    row = Vendor.objects.create(book=book, name=add_check(book, value), created_by=user)
    return {'vendor_id': str(row.pk)}


def _vendor(book, vendor_id):
    try:
        wanted = uuid.UUID(vendor_id) if type(vendor_id) is str else None
    except ValueError:
        wanted = None
    row = book.vendors.filter(pk=wanted).first() if wanted else None
    require(row is not None and str(wanted) == vendor_id, 'vendor_not_found')
    return row


def payment_check(book, value):
    """New payment details for a vendor, prepared against the version they replace and stating how they were verified. Read-only."""
    fields(value, ('vendor_id', 'method', 'details', 'verified_by', 'expected_version'))
    vendor = _vendor(book, value['vendor_id'])
    require(type(value['method']) is str and value['method'] in METHODS)
    details, verified_by = text(value['details'], 500), text(value['verified_by'], 300)
    require(type(value['expected_version']) is int and value['expected_version'] >= 0)
    current = vendor.payments.order_by('-version').first()
    require((current.version if current else 0) == value['expected_version'], 'vendor_payment_changed')
    require(current is None or (current.method, current.details) != (value['method'], details), 'vendor_payment_unchanged')
    # "ok" or "yes" is not a verification; the person should be able to read how it was confirmed.
    require(len(verified_by) >= 10, 'vendor_verification_required')
    return vendor, current, {'method': value['method'], 'details': details, 'verified_by': verified_by, 'version': value['expected_version'] + 1}


def describe_payment(book, value):
    vendor, current, new = payment_check(book, value)
    return {'books': book.entity.name, 'vendor': vendor.name, 'version': new['version'], 'method': new['method'], 'details': new['details'], 'verified_by': new['verified_by'],
            'previous': None if current is None else {'version': current.version, 'method': current.method, 'details': masked(current.details), 'set_on': current.set_on.isoformat()},
            'current': True}


@guarded
@transaction.atomic
def set_payment(user, book_id, value):
    user, book = owner_book(user, book_id)
    vendor, _, new = payment_check(book, value)
    VendorPayment.objects.create(vendor=vendor, created_by=user, set_on=today(), **new)
    return {'vendor_id': str(vendor.pk), 'version': new['version']}


def _shown(row):
    return {'version': row.version, 'method': row.method, 'details': masked(row.details), 'verified_by': row.verified_by, 'set_on': row.set_on.isoformat()}


@guarded
@transaction.atomic
def listing(user, book_id):
    """Every vendor of the book with its current payment details, masked. Read-only."""
    _, book = owned(user, book_id)
    result = []
    for vendor in book.vendors.order_by('name', 'id')[:MAX_VENDORS]:
        versions = list(vendor.payments.order_by('-version'))
        result.append({'id': str(vendor.pk), 'name': vendor.name, 'payment': _shown(versions[0]) if versions else None, 'versions': len(versions)})
    return {'book_id': str(book.pk), 'vendors': result}


@guarded
@transaction.atomic
def history(user, book_id, vendor_id):
    """Every version of one vendor's payment details, newest first, masked. Read-only."""
    _, book = owned(user, book_id)
    vendor = _vendor(book, vendor_id)
    return {'book_id': str(book.pk), 'vendor_id': str(vendor.pk), 'name': vendor.name, 'versions': [_shown(row) for row in vendor.payments.order_by('-version')]}
