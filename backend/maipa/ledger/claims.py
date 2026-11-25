"""Expense claims: business costs someone paid with their own money.

Recording a claim posts each line to its expense account and the total to a liability for what the business owes
that person; reimbursing posts that liability against a bank or cash account; voiding an open claim reverses its
entry. Each is one approval through the ordinary journal path. Only a name is kept for the person.

A receipt reference can be claimed once. A line from the same person for the same date and amount as an earlier
line is allowed, because it can be real, but the approval card shows the earlier one.
"""
import re
import uuid

from django.db import transaction

from .bills import _account, _open_date, _owner
from .models import ExpenseClaim
from .services import closed, confirm_own_journal, day, fields, guarded, money, owned, propose_journal, require, reverse_journal, text, units

MAX_LINES = 50


def _receipt_key(receipt):
    return re.sub(r'[^0-9a-z]', '', receipt.casefold())


def record_check(book, value):
    """A named person's costs, each dated on or before the claim and charged to an expense or non-cash asset account. Read-only."""
    fields(value, ('claimant', 'date', 'lines', 'payable_account_id'))
    claimant, dated = text(value['claimant'], 100), day(value['date'])
    _open_date(book, dated)
    payable = _account(book, value['payable_account_id'])
    require(payable.category == 'liability', 'claim_payable_invalid')
    require(type(value['lines']) is list and 1 <= len(value['lines']) <= MAX_LINES)
    lines = []
    for line in value['lines']:
        require(type(line) is dict and {'date', 'description', 'amount', 'account_id'} <= set(line) <= {'date', 'description', 'amount', 'account_id', 'receipt'})
        when, amount = day(line['date']), units(line['amount'])
        require(amount > 0)
        require(book.period_start <= when <= book.period_end, 'ledger_date_outside_period')
        require(when <= dated, 'claim_line_after_claim')
        account = _account(book, line['account_id'])
        require(account.category == 'expense' or (account.category == 'asset' and not account.cash), 'claim_account_invalid')
        lines.append({'date': when, 'description': text(line['description'], 200), 'amount': amount, 'account': account,
                      'receipt': text(line['receipt'], 100) if 'receipt' in line else None})
    keys = [_receipt_key(line['receipt']) for line in lines if line['receipt']]
    require(len(keys) == len(set(keys)), 'claim_receipt_repeated')
    earlier = list(book.claims.exclude(state='void'))
    taken = {_receipt_key(item['receipt']) for row in earlier for item in row.lines if item.get('receipt')}
    require(not (set(keys) & taken), 'claim_receipt_already_claimed')
    name = ' '.join(claimant.split()).casefold()
    alike = [{'claimant': row.claimant, 'date': item['date'], 'description': item['description'], 'amount': money(units(item['amount'])), 'state': row.state}
             for row in earlier if ' '.join(row.claimant.split()).casefold() == name for item in row.lines
             if any(item['date'] == line['date'].isoformat() and units(item['amount']) == line['amount'] for line in lines)]
    return {'claimant': claimant, 'date': dated, 'payable': payable, 'lines': lines, 'total': sum(line['amount'] for line in lines), 'alike': alike}


def describe(book, value):
    checked = record_check(book, value)
    return {'books': book.entity.name, 'currency': book.currency, 'claimant': checked['claimant'], 'date': checked['date'].isoformat(), 'total': money(checked['total']),
            'payable': f"{checked['payable'].code} {checked['payable'].name}", 'possible_duplicates': checked['alike'], 'current': True,
            'lines': [{'date': line['date'].isoformat(), 'description': line['description'], 'amount': money(line['amount']),
                       'account': f"{line['account'].code} {line['account'].name}", 'receipt': line['receipt']} for line in checked['lines']]}


def _post(user, book, kind, when, description, evidence, lines):
    made = propose_journal(user, str(book.pk), {
        'operation_id': str(uuid.uuid4()), 'expected_revision': book.revision, 'date': when.isoformat(), 'description': description,
        'source': {'kind': kind, 'reference': f'{kind}-{uuid.uuid4()}'}, 'evidence': [evidence], 'lines': lines}, _kind=kind)
    confirm_own_journal(user, str(book.pk), made['id'], {'expected_revision': book.revision + 1, 'payload_hash': made['payload_hash'], 'confirm': True})
    book.refresh_from_db()
    return made['id']


@guarded
@transaction.atomic
def record(user, book_id, value):
    user, book = _owner(user, book_id)
    checked = record_check(book, value)
    entry = [{'account_id': str(line['account'].pk), 'debit': money(line['amount']), 'credit': '0'} for line in checked['lines']]
    entry.append({'account_id': str(checked['payable'].pk), 'debit': '0', 'credit': money(checked['total'])})
    journal = _post(user, book, 'claim', checked['date'], f"Expense claim from {checked['claimant']}"[:500],
                    f"Expense claim of {len(checked['lines'])} line(s) from {checked['claimant']}", entry)
    row = ExpenseClaim.objects.create(book=book, claimant=checked['claimant'], claim_date=checked['date'], total_units=checked['total'], payable_account=checked['payable'],
        lines=[{'date': line['date'].isoformat(), 'description': line['description'], 'amount': money(line['amount']), 'account_id': str(line['account'].pk),
                'receipt': line['receipt']} for line in checked['lines']], journal_id=journal, created_by=user)
    return {'claim_id': str(row.pk), 'journal_id': journal}


def _claim(book, claim_id):
    try:
        wanted = uuid.UUID(claim_id) if type(claim_id) is str else None
    except ValueError:
        wanted = None
    row = book.claims.select_related('payable_account').filter(pk=wanted).first() if wanted else None
    require(row is not None and str(wanted) == claim_id, 'claim_not_found')
    return row


def reimburse_check(book, value):
    fields(value, ('claim_id', 'date', 'cash_account_id'))
    row, when, cash = _claim(book, value['claim_id']), day(value['date']), _account(book, value['cash_account_id'])
    require(row.state == 'open', 'claim_not_open')
    require(cash.cash, 'claim_cash_account_invalid')
    require(book.period_start <= when <= book.period_end, 'ledger_date_outside_period')
    require(when >= row.claim_date, 'claim_payment_before_claim')
    require(not closed(book, when), 'ledger_period_closed')
    return row, when, cash


def describe_reimburse(book, value):
    row, when, cash = reimburse_check(book, value)
    return {'books': book.entity.name, 'currency': book.currency, 'claimant': row.claimant, 'claim_date': row.claim_date.isoformat(), 'total': money(row.total_units),
            'date': when.isoformat(), 'cash_account': f'{cash.code} {cash.name}', 'payable': f'{row.payable_account.code} {row.payable_account.name}', 'current': True}


@guarded
@transaction.atomic
def reimburse(user, book_id, value):
    user, book = _owner(user, book_id)
    row, when, cash = reimburse_check(book, value)
    amount = money(row.total_units)
    journal = _post(user, book, 'claimpay', when, f'Reimbursement to {row.claimant}'[:500], f'Reimbursement of the expense claim of {row.claim_date.isoformat()}',
                    [{'account_id': str(row.payable_account_id), 'debit': amount, 'credit': '0'}, {'account_id': str(cash.pk), 'debit': '0', 'credit': amount}])
    row.state, row.paid_on, row.payment_journal_id = 'paid', when, journal
    row.save(update_fields=('state', 'paid_on', 'payment_journal'))
    return {'claim_id': str(row.pk), 'journal_id': journal, 'paid_on': when.isoformat()}


def void_check(book, value):
    fields(value, ('claim_id', 'date'))
    row, when = _claim(book, value['claim_id']), day(value['date'])
    require(row.state == 'open', 'claim_not_open')
    _open_date(book, when)
    require(when >= row.claim_date, 'claim_void_before_claim')
    return row, when


def describe_void(book, value):
    row, when = void_check(book, value)
    return {'books': book.entity.name, 'currency': book.currency, 'claimant': row.claimant, 'claim_date': row.claim_date.isoformat(), 'total': money(row.total_units),
            'date': when.isoformat(), 'current': True}


@guarded
@transaction.atomic
def void(user, book_id, value):
    user, book = _owner(user, book_id)
    row, when = void_check(book, value)
    undone = reverse_journal(user, book_id, str(row.journal_id), {'expected_revision': book.revision, 'date': when.isoformat()})
    row.state, row.void_journal_id = 'void', undone['journal_id']
    row.save(update_fields=('state', 'void_journal'))
    return {'claim_id': str(row.pk), 'journal_id': undone['journal_id']}


@guarded
@transaction.atomic
def outstanding(user, book_id):
    """Open claims grouped by person, with what the business owes each, and the last claims settled. Read-only."""
    _, book = owned(user, book_id)
    rows = list(book.claims.order_by('claim_date', 'created_at'))
    people = {}
    for row in rows:
        if row.state == 'open':
            people.setdefault(row.claimant, []).append(row)
    settled = sorted((row for row in rows if row.state != 'open'), key=lambda row: row.created_at, reverse=True)[:10]
    return {'book_id': str(book.pk), 'currency': book.currency, 'total': money(sum(row.total_units for row in rows if row.state == 'open')),
            'people': [{'claimant': name, 'total': money(sum(row.total_units for row in mine)),
                        'claims': [{'id': str(row.pk), 'date': row.claim_date.isoformat(), 'total': money(row.total_units), 'lines': len(row.lines)} for row in mine]}
                       for name, mine in sorted(people.items(), key=lambda item: item[0].casefold())],
            'recent': [{'id': str(row.pk), 'claimant': row.claimant, 'total': money(row.total_units), 'state': row.state,
                        'paid_on': row.paid_on.isoformat() if row.paid_on else None} for row in settled]}
