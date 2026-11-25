"""Bills: what the business owes its vendors.

Recording a bill posts the expense (or asset) against a payable account; paying it posts the payable against a bank
or cash account; voiding an unpaid bill reverses its entry. Each is one approval and goes through the same journal
path as every other entry, so period locks, numbering and the integrity check apply unchanged.

The same vendor reference is never entered twice. A bill from the same vendor for the same amount within ten days
is not blocked, because it can be legitimate, but the approval card shows the earlier one.
"""
from datetime import timedelta
import re
import uuid

from django.db import transaction

from .models import Bill, JournalLine
from .services import (LedgerError, closed, confirm_own_journal, day, fields, guarded, identity, money, owned, propose_journal, require, reverse_journal, text, units)
from .vendors import _vendor

LOOK_ALIKE_DAYS = 10
MAX_OPEN = 1000


def _reference_key(reference):
    return re.sub(r'[^0-9a-z]', '', reference.casefold())


def _account(book, account_id):
    account = book.accounts.filter(pk=identity(account_id)).first()
    require(account is not None, 'ledger_account_not_found')
    return account


def _open_date(book, when):
    require(book.period_start <= when <= book.period_end, 'ledger_date_outside_period')
    require(not closed(book, when), 'ledger_period_closed')


def record_check(book, value):
    """A vendor's bill with what it is for and which payable it sits in, not already entered. Read-only."""
    fields(value, ('vendor_id', 'reference', 'bill_date', 'due_date', 'amount', 'description', 'account_id', 'payable_account_id'))
    vendor = _vendor(book, value['vendor_id'])
    reference, description = text(value['reference'], 100), text(value['description'], 200)
    bill_date, due_date, amount = day(value['bill_date']), day(value['due_date']), units(value['amount'])
    require(amount > 0)
    require(due_date >= bill_date, 'bill_due_before_date')
    _open_date(book, bill_date)
    account, payable = _account(book, value['account_id']), _account(book, value['payable_account_id'])
    require(account.category == 'expense' or (account.category == 'asset' and not account.cash), 'bill_account_invalid')
    require(payable.category == 'liability', 'bill_payable_invalid')
    earlier = [row for row in vendor.bills.exclude(state='void')]
    require(_reference_key(reference) not in {_reference_key(row.reference) for row in earlier}, 'bill_duplicate_reference')
    require(book.bills.filter(state='open').count() < MAX_OPEN, 'bill_limit')
    alike = sorted((row for row in earlier if row.amount_units == amount and abs((row.bill_date - bill_date).days) <= LOOK_ALIKE_DAYS), key=lambda row: (row.bill_date, row.reference))
    return {'vendor': vendor, 'reference': reference, 'description': description, 'bill_date': bill_date, 'due_date': due_date, 'amount_units': amount,
            'account': account, 'payable': payable, 'alike': alike}


def describe(book, value):
    checked = record_check(book, value)
    return {'books': book.entity.name, 'currency': book.currency, 'vendor': checked['vendor'].name, 'reference': checked['reference'], 'bill_date': checked['bill_date'].isoformat(),
            'due_date': checked['due_date'].isoformat(), 'amount': money(checked['amount_units']), 'description': checked['description'],
            'account': f"{checked['account'].code} {checked['account'].name}", 'payable': f"{checked['payable'].code} {checked['payable'].name}",
            'possible_duplicates': [{'reference': row.reference, 'bill_date': row.bill_date.isoformat(), 'amount': money(row.amount_units), 'state': row.state} for row in checked['alike']],
            'current': True}


def _owner(user, book_id):
    user, book = owned(user, book_id, True)
    require(book.created_by_id == user.pk, 'ledger_confirmation_denied')
    return user, book


def _post(user, book, kind, when, description, evidence, debit, credit, amount):
    """One balanced two-line entry, proposed and posted in the caller's transaction."""
    made = propose_journal(user, str(book.pk), {
        'operation_id': str(uuid.uuid4()), 'expected_revision': book.revision, 'date': when.isoformat(), 'description': description,
        'source': {'kind': kind, 'reference': f'{kind}-{uuid.uuid4()}'}, 'evidence': [evidence],
        'lines': [{'account_id': str(debit.pk), 'debit': money(amount), 'credit': '0'}, {'account_id': str(credit.pk), 'debit': '0', 'credit': money(amount)}]}, _kind=kind)
    confirm_own_journal(user, str(book.pk), made['id'], {'expected_revision': book.revision + 1, 'payload_hash': made['payload_hash'], 'confirm': True})
    book.refresh_from_db()
    return made['id']


@guarded
@transaction.atomic
def record(user, book_id, value):
    user, book = _owner(user, book_id)
    checked = record_check(book, value)
    journal = _post(user, book, 'bill', checked['bill_date'], f"{checked['vendor'].name} {checked['reference']}: {checked['description']}"[:500],
                    f"Bill {checked['reference']} from {checked['vendor'].name}, due {checked['due_date'].isoformat()}", checked['account'], checked['payable'], checked['amount_units'])
    row = Bill.objects.create(book=book, vendor=checked['vendor'], reference=checked['reference'], description=checked['description'], bill_date=checked['bill_date'],
                              due_date=checked['due_date'], amount_units=checked['amount_units'], account=checked['account'], payable_account=checked['payable'],
                              journal_id=journal, created_by=user)
    return {'bill_id': str(row.pk), 'journal_id': journal}


def _bill(book, bill_id):
    try:
        wanted = uuid.UUID(bill_id) if type(bill_id) is str else None
    except ValueError:
        wanted = None
    row = book.bills.select_related('vendor', 'payable_account').filter(pk=wanted).first() if wanted else None
    require(row is not None and str(wanted) == bill_id, 'bill_not_found')
    return row


def pay_check(book, value):
    fields(value, ('bill_id', 'date', 'cash_account_id'))
    row, when, cash = _bill(book, value['bill_id']), day(value['date']), _account(book, value['cash_account_id'])
    require(row.state == 'open', 'bill_not_open')
    require(cash.cash, 'bill_cash_account_invalid')
    require(book.period_start <= when <= book.period_end, 'ledger_date_outside_period')
    require(when >= row.bill_date, 'bill_payment_before_bill')
    require(not closed(book, when), 'ledger_period_closed')
    return row, when, cash


def describe_pay(book, value):
    row, when, cash = pay_check(book, value)
    return {'books': book.entity.name, 'currency': book.currency, 'vendor': row.vendor.name, 'reference': row.reference, 'amount': money(row.amount_units), 'date': when.isoformat(),
            'cash_account': f'{cash.code} {cash.name}', 'payable': f'{row.payable_account.code} {row.payable_account.name}', 'due_date': row.due_date.isoformat(), 'current': True}


@guarded
@transaction.atomic
def pay(user, book_id, value):
    user, book = _owner(user, book_id)
    row, when, cash = pay_check(book, value)
    journal = _post(user, book, 'billpay', when, f'Payment to {row.vendor.name} for {row.reference}'[:500], f'Payment of bill {row.reference} from {row.vendor.name}',
                    row.payable_account, cash, row.amount_units)
    row.state, row.paid_on, row.payment_journal_id = 'paid', when, journal
    row.save(update_fields=('state', 'paid_on', 'payment_journal'))
    return {'bill_id': str(row.pk), 'journal_id': journal, 'paid_on': when.isoformat()}


def void_check(book, value):
    fields(value, ('bill_id', 'date'))
    row, when = _bill(book, value['bill_id']), day(value['date'])
    require(row.state == 'open', 'bill_not_open')
    _open_date(book, when)
    require(when >= row.bill_date, 'bill_void_before_bill')
    return row, when


def describe_void(book, value):
    row, when = void_check(book, value)
    return {'books': book.entity.name, 'currency': book.currency, 'vendor': row.vendor.name, 'reference': row.reference, 'amount': money(row.amount_units),
            'bill_date': row.bill_date.isoformat(), 'date': when.isoformat(), 'current': True}


@guarded
@transaction.atomic
def void(user, book_id, value):
    user, book = _owner(user, book_id)
    row, when = void_check(book, value)
    undone = reverse_journal(user, book_id, str(row.journal_id), {'expected_revision': book.revision, 'date': when.isoformat()})
    row.state, row.void_journal_id = 'void', undone['journal_id']
    row.save(update_fields=('state', 'void_journal'))
    return {'bill_id': str(row.pk), 'journal_id': undone['journal_id']}


@guarded
@transaction.atomic
def payables(user, book_id, value):
    """Unpaid bills as of a date, overdue first, with their total beside the ledger balance of the payable accounts. Read-only."""
    _, book = owned(user, book_id)
    fields(value, ('as_of',))
    as_of = day(value['as_of'])
    rows = list(book.bills.select_related('vendor').order_by('due_date', 'bill_date', 'reference'))
    unpaid = [row for row in rows if row.state == 'open']
    unpaid.sort(key=lambda row: (row.due_date >= as_of, row.due_date, row.reference))
    late = [row for row in unpaid if row.due_date < as_of]
    accounts = {row.payable_account_id for row in rows}
    ledger_balance = sum(line.credit_units - line.debit_units for line in JournalLine.objects.filter(account_id__in=accounts, journal__book=book, journal__state='posted'))
    open_total = sum(row.amount_units for row in unpaid)
    settled = sorted((row for row in rows if row.state != 'open'), key=lambda row: row.created_at, reverse=True)[:10]
    return {'book_id': str(book.pk), 'currency': book.currency, 'as_of': as_of.isoformat(),
            'bills': [{'id': str(row.pk), 'vendor': row.vendor.name, 'reference': row.reference, 'description': row.description, 'bill_date': row.bill_date.isoformat(),
                       'due_date': row.due_date.isoformat(), 'amount': money(row.amount_units), 'state': 'overdue' if row.due_date < as_of else 'open',
                       'days_overdue': max((as_of - row.due_date).days, 0)} for row in unpaid],
            'open_total': money(open_total), 'overdue_total': money(sum(row.amount_units for row in late)),
            # The two differ when something was entered on a payable account outside a bill; shown, never adjusted.
            'ledger_payable': money(ledger_balance), 'difference': money(ledger_balance - open_total),
            'recent': [{'id': str(row.pk), 'vendor': row.vendor.name, 'reference': row.reference, 'amount': money(row.amount_units), 'state': row.state,
                        'paid_on': row.paid_on.isoformat() if row.paid_on else None} for row in settled]}
