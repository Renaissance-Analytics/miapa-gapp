"""Budgets and variance.

A budget is an amount the person approved for one income or expense account in one month. It is a plan, kept apart
from the books: no entry, no balance, no book revision. Variance compares it with posted entries for whole months.
An account with activity and no budget is shown as unbudgeted; silence is never read as a budget of zero.
"""
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
import re

from django.db import transaction

from .models import BudgetLine
from .services import _statements, balance_units, fields, guarded, identity, money, owned, require, units

MAX_LINES = 100
MAX_MONTHS = 36


def _month(value):
    require(type(value) is str and re.fullmatch(r'[0-9]{4}-(0[1-9]|1[0-2])', value) is not None)
    return date(int(value[:4]), int(value[5:]), 1)


def _months(book, value):
    """The first day of every month from `month` through `through` (default the same month), inside the book period."""
    first = _month(value['month'])
    last = _month(value['through']) if 'through' in value else first
    require(last >= first, 'budget_range_reversed')
    found, when = [], first
    while when <= last:
        require(len(found) < MAX_MONTHS)
        found.append(when)
        when = date(when.year + (when.month == 12), when.month % 12 + 1, 1)
    require(book.period_start.replace(day=1) <= first and last <= book.period_end, 'ledger_date_outside_period')
    return found


def _last_day(first):
    return date(first.year + (first.month == 12), first.month % 12 + 1, 1) - timedelta(days=1)


def set_check(book, value):
    """Amounts for income or expense accounts of this book, for one month or a run of months. Read-only."""
    require(type(value) is dict and {'month', 'lines'} <= set(value) <= {'month', 'through', 'lines'})
    months = _months(book, value)
    require(type(value['lines']) is list and 0 < len(value['lines']) <= MAX_LINES)
    accounts, lines = {str(account.pk): account for account in book.accounts.all()}, []
    for line in value['lines']:
        fields(line, ('account_id', 'amount'))
        account = accounts.get(str(identity(line['account_id'])))
        require(account is not None, 'ledger_account_not_found')
        require(account.category in ('income', 'expense'), 'budget_account_not_income_or_expense')
        lines.append((account, units(line['amount'])))
    require(len({account.pk for account, _ in lines}) == len(lines), 'budget_account_repeated')
    lines.sort(key=lambda item: (item[0].category != 'income', item[0].code))
    return months, lines


def describe(book, value):
    months, lines = set_check(book, value)
    return {'books': book.entity.name, 'currency': book.currency, 'month': months[0].strftime('%Y-%m'), 'through': months[-1].strftime('%Y-%m'), 'months': len(months), 'current': True,
            'lines': [{'account': f'{account.code} {account.name}', 'category': account.category, 'amount': money(amount)} for account, amount in lines]}


@guarded
@transaction.atomic
def set_budget(user, book_id, value):
    """Save the approved amounts, replacing any earlier amount for the same account and month. The books do not change."""
    user, book = owned(user, book_id, True)
    require(book.created_by_id == user.pk, 'ledger_confirmation_denied')
    months, lines = set_check(book, value)
    for month in months:
        for account, amount in lines:
            BudgetLine.objects.update_or_create(book=book, account=account, month=month, defaults={'amount_units': amount, 'created_by': user})
    return {'months': len(months), 'lines': len(months) * len(lines)}


def clear_check(book, value):
    require(type(value) is dict and {'month'} <= set(value) <= {'month', 'through'})
    months = _months(book, value)
    found = book.budget_lines.filter(month__in=months)
    require(found.exists(), 'budget_nothing_to_clear')
    return months, found


def describe_clear(book, value):
    months, found = clear_check(book, value)
    return {'books': book.entity.name, 'month': months[0].strftime('%Y-%m'), 'through': months[-1].strftime('%Y-%m'), 'months': len(months), 'lines': found.count(), 'current': True}


@guarded
@transaction.atomic
def clear_budget(user, book_id, value):
    user, book = owned(user, book_id, True)
    require(book.created_by_id == user.pk, 'ledger_confirmation_denied')
    months, found = clear_check(book, value)
    count = found.count()
    found.delete()
    return {'months': len(months), 'lines': count}


@guarded
@transaction.atomic
def variance(user, book_id, value):
    """Actual against budget for whole months, account by account. Read-only."""
    _, book = owned(user, book_id)
    fields(value, ('from', 'to'))
    months = _months(book, {'month': value['from'], 'through': value['to']})
    start, end = max(months[0], book.period_start), min(_last_day(months[-1]), book.period_end)
    accounts = {account.pk: account for account in book.accounts.all()}
    statements, totals = _statements(book, accounts, start, end)
    actual = {item['id']: balance_units(item['amount']) for key in ('income', 'expenses') for item in statements['income_statement'][key]}
    budget, counted = {}, {}
    for line in book.budget_lines.filter(month__in=months):
        budget[line.account_id] = budget.get(line.account_id, 0) + line.amount_units
        counted[line.account_id] = counted.get(line.account_id, 0) + 1
    rows, sums = [], {'income': [0, 0], 'expense': [0, 0]}
    unbudgeted = {'income': 0, 'expense': 0}
    for account in sorted(accounts.values(), key=lambda item: (item.category != 'income', item.code)):
        if account.category not in ('income', 'expense'):
            continue
        spent, planned = actual.get(str(account.pk), 0), budget.get(account.pk)
        if planned is None and spent == 0:
            continue
        if planned is None:
            unbudgeted[account.category] += spent
            rows.append({'code': account.code, 'name': account.name, 'category': account.category, 'budget': None, 'budget_months': 0, 'actual': money(spent),
                         'variance': None, 'percent': None, 'favourable': None})
            continue
        sums[account.category][0] += planned
        sums[account.category][1] += spent
        difference = spent - planned
        rows.append({'code': account.code, 'name': account.name, 'category': account.category, 'budget': money(planned), 'budget_months': counted[account.pk],
                     'actual': money(spent), 'variance': money(difference),
                     'percent': str((Decimal(difference * 100) / Decimal(planned)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)) if planned else None,
                     'favourable': difference >= 0 if account.category == 'income' else difference <= 0})
    side = lambda key: {'budget': money(sums[key][0]), 'actual': money(sums[key][1]), 'variance': money(sums[key][1] - sums[key][0])}
    return {'book_id': str(book.pk), 'currency': book.currency, 'revision': book.revision, 'from': months[0].strftime('%Y-%m'), 'to': months[-1].strftime('%Y-%m'),
            'start': start.isoformat(), 'end': end.isoformat(), 'months': len(months), 'budgeted': bool(budget), 'rows': rows,
            'totals': {'income': side('income'), 'expenses': side('expense'), 'unbudgeted_income': money(unbudgeted['income']), 'unbudgeted_expenses': money(unbudgeted['expense']),
                       'net_budget': money(sums['income'][0] - sums['expense'][0]), 'net_actual': money(totals['net_income'])}}
