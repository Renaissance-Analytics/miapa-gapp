"""Customers and invoices: what customers owe the business.

Recording an invoice posts its total to a receivable account and each line to its income account; voiding an
unpaid invoice reverses that entry. Each is one approval through the ordinary journal path, so period locks,
numbering and the integrity check apply unchanged.

A line's amount is its quantity times its unit price, rounded half up to the cent, computed here and shown on the
approval card. An invoice number is never used twice in a book, even after the invoice is voided, so a number
always points to one document. Only a name is kept for a customer.
"""
from decimal import Decimal, ROUND_HALF_UP
import re
import uuid

from django.db import transaction

from .bills import _account, _open_date, _owner
from .claims import _post
from .models import Customer, Invoice, JournalLine
from .services import day, fields, guarded, money, owned, require, reverse_journal, text, units

MAX_CUSTOMERS = 500
MAX_LINES = 50
MAX_OPEN = 1000


def _key(name):
    return ' '.join(name.split()).casefold()


def _number_key(number):
    return re.sub(r'[^0-9a-z]', '', number.casefold())


def add_check(book, value):
    fields(value, ('name',))
    name = text(value['name'], 200)
    require(_key(name) not in {_key(existing) for existing in book.customers.values_list('name', flat=True)}, 'customer_exists')
    require(book.customers.count() < MAX_CUSTOMERS, 'customer_limit')
    return name


@guarded
@transaction.atomic
def add(user, book_id, value):
    user, book = _owner(user, book_id)
    row = Customer.objects.create(book=book, name=add_check(book, value), created_by=user)
    return {'customer_id': str(row.pk)}


def _customer(book, customer_id):
    try:
        wanted = uuid.UUID(customer_id) if type(customer_id) is str else None
    except ValueError:
        wanted = None
    row = book.customers.filter(pk=wanted).first() if wanted else None
    require(row is not None and str(wanted) == customer_id, 'customer_not_found')
    return row


def line_amount(quantity, unit_price):
    """Quantity times unit price in ten-thousandths, rounded half up to the cent."""
    exact = Decimal(quantity) * Decimal(unit_price) / Decimal(10000)
    return int((exact / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)) * 100


def record_check(book, value):
    """An invoice to a customer with a number not used before in the book, priced line by line to income accounts. Read-only."""
    fields(value, ('customer_id', 'number', 'invoice_date', 'due_date', 'lines', 'receivable_account_id'))
    customer = _customer(book, value['customer_id'])
    number = text(value['number'], 40)
    require(_number_key(number) != '', 'invoice_number_invalid')
    invoice_date, due_date = day(value['invoice_date']), day(value['due_date'])
    require(due_date >= invoice_date, 'invoice_due_before_date')
    _open_date(book, invoice_date)
    receivable = _account(book, value['receivable_account_id'])
    require(receivable.category == 'asset' and not receivable.cash, 'invoice_receivable_invalid')
    require(type(value['lines']) is list and 1 <= len(value['lines']) <= MAX_LINES)
    lines = []
    for line in value['lines']:
        fields(line, ('description', 'quantity', 'unit_price', 'account_id'))
        quantity, price = units(line['quantity']), units(line['unit_price'])
        require(quantity > 0 and price > 0)
        amount = line_amount(quantity, price)
        require(amount > 0, 'invoice_line_zero')
        account = _account(book, line['account_id'])
        require(account.category == 'income', 'invoice_account_invalid')
        lines.append({'description': text(line['description'], 200), 'quantity': quantity, 'unit_price': price, 'amount': amount, 'account': account})
    require(_number_key(number) not in {_number_key(row) for row in book.invoices.values_list('number', flat=True)}, 'invoice_duplicate_number')
    require(book.invoices.filter(state='open').count() < MAX_OPEN, 'invoice_limit')
    return {'customer': customer, 'number': number, 'invoice_date': invoice_date, 'due_date': due_date, 'receivable': receivable,
            'lines': lines, 'total': sum(line['amount'] for line in lines)}


def describe(book, value):
    checked = record_check(book, value)
    return {'books': book.entity.name, 'currency': book.currency, 'customer': checked['customer'].name, 'number': checked['number'],
            'invoice_date': checked['invoice_date'].isoformat(), 'due_date': checked['due_date'].isoformat(), 'total': money(checked['total']),
            'receivable': f"{checked['receivable'].code} {checked['receivable'].name}", 'current': True,
            'lines': [{'description': line['description'], 'quantity': money(line['quantity']), 'unit_price': money(line['unit_price']),
                       'amount': money(line['amount']), 'account': f"{line['account'].code} {line['account'].name}"} for line in checked['lines']]}


@guarded
@transaction.atomic
def record(user, book_id, value):
    user, book = _owner(user, book_id)
    checked = record_check(book, value)
    entry = [{'account_id': str(checked['receivable'].pk), 'debit': money(checked['total']), 'credit': '0'}]
    entry += [{'account_id': str(line['account'].pk), 'debit': '0', 'credit': money(line['amount'])} for line in checked['lines']]
    journal = _post(user, book, 'invoice', checked['invoice_date'], f"Invoice {checked['number']} to {checked['customer'].name}"[:500],
                    f"Invoice {checked['number']} to {checked['customer'].name}, due {checked['due_date'].isoformat()}", entry)
    row = Invoice.objects.create(book=book, customer=checked['customer'], number=checked['number'], invoice_date=checked['invoice_date'],
        due_date=checked['due_date'], total_units=checked['total'], receivable_account=checked['receivable'], journal_id=journal, created_by=user,
        lines=[{'description': line['description'], 'quantity': money(line['quantity']), 'unit_price': money(line['unit_price']),
                'amount': money(line['amount']), 'account_id': str(line['account'].pk)} for line in checked['lines']])
    return {'invoice_id': str(row.pk), 'journal_id': journal}


def _invoice(book, invoice_id):
    try:
        wanted = uuid.UUID(invoice_id) if type(invoice_id) is str else None
    except ValueError:
        wanted = None
    row = book.invoices.select_related('customer').filter(pk=wanted).first() if wanted else None
    require(row is not None and str(wanted) == invoice_id, 'invoice_not_found')
    return row


def void_check(book, value):
    fields(value, ('invoice_id', 'date'))
    row, when = _invoice(book, value['invoice_id']), day(value['date'])
    require(row.state == 'open', 'invoice_not_open')
    _open_date(book, when)
    require(when >= row.invoice_date, 'invoice_void_before_invoice')
    return row, when


def describe_void(book, value):
    row, when = void_check(book, value)
    return {'books': book.entity.name, 'currency': book.currency, 'customer': row.customer.name, 'number': row.number, 'total': money(row.total_units),
            'invoice_date': row.invoice_date.isoformat(), 'date': when.isoformat(), 'current': True}


@guarded
@transaction.atomic
def void(user, book_id, value):
    user, book = _owner(user, book_id)
    row, when = void_check(book, value)
    undone = reverse_journal(user, book_id, str(row.journal_id), {'expected_revision': book.revision, 'date': when.isoformat()})
    row.state, row.void_journal_id = 'void', undone['journal_id']
    row.save(update_fields=('state', 'void_journal'))
    return {'invoice_id': str(row.pk), 'journal_id': undone['journal_id']}


def next_number(numbers):
    """The number after the most recent one that ends in digits, keeping its prefix and width; None when there is none."""
    for number in numbers:
        found = re.fullmatch(r'(.*?)([0-9]+)', number)
        if found:
            return found.group(1) + str(int(found.group(2)) + 1).zfill(len(found.group(2)))
    return None


@guarded
@transaction.atomic
def receivables(user, book_id, value):
    """Customers and their unpaid invoices as of a date, overdue first, with the total beside the ledger balance of the receivable accounts. Read-only."""
    _, book = owned(user, book_id)
    fields(value, ('as_of',))
    as_of = day(value['as_of'])
    rows = list(book.invoices.select_related('customer').order_by('due_date', 'invoice_date', 'number'))
    unpaid = sorted((row for row in rows if row.state == 'open'), key=lambda row: (row.due_date >= as_of, row.due_date, row.number))
    late = [row for row in unpaid if row.due_date < as_of]
    accounts = {row.receivable_account_id for row in rows}
    ledger_balance = sum(line.debit_units - line.credit_units for line in JournalLine.objects.filter(account_id__in=accounts, journal__book=book, journal__state='posted'))
    open_total = sum(row.total_units for row in unpaid)
    newest = [row.number for row in sorted(rows, key=lambda row: row.created_at, reverse=True)]
    settled = sorted((row for row in rows if row.state != 'open'), key=lambda row: row.created_at, reverse=True)[:10]
    return {'book_id': str(book.pk), 'currency': book.currency, 'as_of': as_of.isoformat(),
            'customers': [{'id': str(row.pk), 'name': row.name} for row in book.customers.order_by('name')],
            'invoices': [{'id': str(row.pk), 'customer': row.customer.name, 'number': row.number, 'invoice_date': row.invoice_date.isoformat(),
                          'due_date': row.due_date.isoformat(), 'total': money(row.total_units), 'state': 'overdue' if row.due_date < as_of else 'open',
                          'days_overdue': max((as_of - row.due_date).days, 0), 'lines': len(row.lines)} for row in unpaid],
            'open_total': money(open_total), 'overdue_total': money(sum(row.total_units for row in late)),
            # The two differ when something was entered on a receivable account outside an invoice; shown, never adjusted.
            'ledger_receivable': money(ledger_balance), 'difference': money(ledger_balance - open_total),
            'next_number': next_number(newest),
            'recent': [{'id': str(row.pk), 'customer': row.customer.name, 'number': row.number, 'total': money(row.total_units), 'state': row.state}
                       for row in settled]}
