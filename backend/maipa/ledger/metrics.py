"""The metric dictionary: named figures that explain themselves.

Each metric carries its definition in plain words, the statement line it ties to, and the inputs it was computed
from. Money comes straight from the same statements as the financial report, in exact units. Percentages and weeks
are computed from those inputs with decimal arithmetic and rounded half up. A metric that cannot be computed is
returned as not available with a reason; nothing is estimated.
"""
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction

from .models import JournalLine
from .services import _statements, balance_units, day, fields, guarded, money, owned, require


def _ratio(numerator, denominator, places):
    return str((Decimal(numerator) / Decimal(denominator)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def _metric(key, name, unit, definition, source, inputs, value=None, reason=None):
    return {'key': key, 'name': name, 'unit': unit, 'available': reason is None, 'value': None if reason else value, 'reason': reason,
            'definition': definition, 'source': source, 'inputs': [{'label': label, 'value': amount} for label, amount in inputs]}


@guarded
@transaction.atomic
def dictionary(user, book_id, value):
    """Every defined metric for a date range. Read-only."""
    _, book = owned(user, book_id)
    fields(value, ('from', 'to'))
    start, end = day(value['from']), day(value['to'])
    accounts = {a.pk: a for a in book.accounts.all()}
    statements, totals = _statements(book, accounts, start, end)
    days = (end - start).days + 1
    income, sheet, flow = statements['income_statement'], statements['balance_sheet'], statements['cash_flow']
    revenue, expenses, net = totals['total_income'], totals['total_expenses'], totals['net_income']
    cash_accounts = [a for a in accounts.values() if a.cash]
    restricted = sum(balance_units(item['closing']) for item in flow['accounts'] if accounts_by_id(accounts, item['id']).restricted)
    cash = balance_units(flow['closing_cash'])
    usable = cash - restricted
    # Change in usable cash inside the range, for the runway: posted lines on unrestricted bank and cash accounts.
    usable_ids = [a.pk for a in cash_accounts if not a.restricted]
    usable_change = sum(line.debit_units - line.credit_units for line in JournalLine.objects.filter(
        account_id__in=usable_ids, journal__book=book, journal__state='posted', journal__date__gte=start, journal__date__lte=end))
    no_cash = None if cash_accounts else 'no_cash_accounts'
    found = [
        _metric('revenue', 'Revenue', 'money', 'Income earned in the period, from posted entries.', 'Income statement: total income', [], income['total_income']),
        _metric('expenses', 'Expenses', 'money', 'Costs recorded in the period, from posted entries.', 'Income statement: total expenses', [], income['total_expenses']),
        _metric('net_income', 'Net income', 'money', 'Revenue minus expenses for the period, from posted entries.', 'Income statement: net income',
                [('Revenue', income['total_income']), ('Expenses', income['total_expenses'])], income['net_income']),
        _metric('net_margin', 'Net margin', 'percent', 'Net income as a percentage of revenue for the period.', 'Income statement: net income and total income',
                [('Net income', income['net_income']), ('Revenue', income['total_income'])],
                _ratio(net * 100, revenue, 2) if revenue else None, None if revenue else 'no_revenue'),
    ]
    previous_end = start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=days - 1)
    change_text = 'Revenue for the period compared with the period of the same length just before it, as a percentage of that earlier revenue.'
    if previous_start < book.period_start:
        found.append(_metric('revenue_change', 'Revenue change', 'percent', change_text, 'Income statement: total income, this period and the one before',
                             [('Revenue this period', income['total_income'])], reason='previous_period_outside_books'))
    else:
        earlier = _statements(book, accounts, previous_start, previous_end)[1]['total_income']
        label = f'{previous_start.isoformat()} to {previous_end.isoformat()}'
        found.append(_metric('revenue_change', 'Revenue change', 'percent', change_text, f'Income statement: total income, this period and {label}',
                             [('Revenue this period', income['total_income']), (f'Revenue {label}', money(earlier))],
                             _ratio((revenue - earlier) * 100, earlier, 2) if earlier else None, None if earlier else 'no_previous_revenue'))
    weekly = Decimal(-usable_change * 7) / Decimal(days) if usable_change < 0 else None
    weekly_text = None if weekly is None else str((weekly / 10000).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP))
    found += [
        _metric('cash', 'Cash', 'money', 'Money in all bank and cash accounts at the end of the period.', 'Cash flow: closing cash', [], flow['closing_cash'], no_cash),
        _metric('usable_cash', 'Usable cash', 'money', 'Cash at the end of the period that is not set aside as restricted.', 'Cash flow: closing cash, less restricted accounts',
                [('Cash in bank and cash accounts', flow['closing_cash']), ('Set aside (restricted)', money(restricted))], money(usable), no_cash),
        _metric('net_cash_change', 'Net cash change', 'money', 'How much cash in all bank and cash accounts went up or down during the period.', 'Cash flow: net change',
                [('Cash at the start', flow['opening_cash']), ('Cash at the end', flow['closing_cash'])], flow['net_change'], no_cash),
        _metric('cash_runway', 'Cash runway', 'weeks',
                'How many weeks usable cash would last if it kept falling at the average weekly rate of this period. Only given when usable cash fell.',
                'Usable cash, and posted movements of unrestricted bank and cash accounts in the period',
                [('Usable cash', money(usable))] + ([('Average weekly decrease in usable cash', weekly_text)] if weekly_text else []),
                _ratio(Decimal(usable), weekly, 1) if weekly else None,
                no_cash or (None if weekly else 'cash_not_decreasing') or ('no_usable_cash' if usable <= 0 else None)),
        _metric('total_assets', 'Total assets', 'money', 'Everything the business owns or is owed at the end of the period.', 'Balance sheet: total assets', [], sheet['total_assets']),
        _metric('total_liabilities', 'Total liabilities', 'money', 'Everything the business owes at the end of the period.', 'Balance sheet: total liabilities', [], sheet['total_liabilities']),
        _metric('net_worth', 'Net worth', 'money', 'Total assets minus total liabilities at the end of the period.', 'Balance sheet: total assets and total liabilities',
                [('Total assets', sheet['total_assets']), ('Total liabilities', sheet['total_liabilities'])], money(totals['total_assets'] - totals['total_liabilities'])),
    ]
    require(len({item['key'] for item in found}) == len(found), 'ledger_invariant_failed')
    return {'book_id': str(book.pk), 'currency': book.currency, 'revision': book.revision, 'from': start.isoformat(), 'to': end.isoformat(), 'days': days, 'metrics': found}


def accounts_by_id(accounts, identity):
    return next(account for account in accounts.values() if str(account.pk) == identity)
