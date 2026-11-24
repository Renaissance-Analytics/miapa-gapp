"""Planned cash items and scenarios.

A plan is what the person expects but has not booked: an amount of cash in or out on a date, optionally repeating
every week or month. Plans are never ledger entries and never reach a statement. The forecast keeps the books-only
weeks exactly as they are and adds a second track, `plan`: the same weeks with the approved plan on top, and with
one named scenario on top of that when asked for.
"""
from datetime import date, timedelta
import uuid

from django.db import transaction
from django.utils import timezone

from .models import CashPlanItem
from .services import (FORECAST_WEEKS, balance_units, cash_forecast, day, guarded, money, owned, require, signed_units, text)

MAX_ITEMS = 200
EVERY = ('week', 'month')
OPTIONAL = ('every', 'until', 'scenario')


def plan_check(book, value):
    """One planned cash movement inside the book period, optionally repeating, optionally part of a named scenario. Read-only."""
    require(type(value) is dict and {'description', 'amount', 'date'} <= set(value) <= {'description', 'amount', 'date', *OPTIONAL})
    description, amount, when = text(value['description'], 200), signed_units(value['amount']), day(value['date'])
    require(book.period_start <= when <= book.period_end, 'ledger_date_outside_period')
    every = value.get('every')
    require(every is None or (type(every) is str and every in EVERY))
    until = day(value['until']) if 'until' in value else None
    require(until is None or every is not None, 'plan_until_needs_repeat')
    require(until is None or until >= when, 'plan_until_before_date')
    scenario = text(value['scenario'], 60) if 'scenario' in value else None
    require(book.plan_items.filter(removed_at=None).count() < MAX_ITEMS, 'plan_limit')
    return {'description': description, 'amount_units': amount, 'date': when, 'every': every, 'until': until, 'scenario': scenario}


def _value(row):
    return {'id': str(row.pk), 'description': row.description, 'amount': money(row.amount_units), 'date': row.date.isoformat(),
            'every': row.every, 'until': row.until.isoformat() if row.until else None, 'scenario': row.scenario}


def _item(book, item_id):
    try:
        wanted = uuid.UUID(item_id) if type(item_id) is str else None
    except ValueError:
        wanted = None
    row = book.plan_items.filter(pk=wanted, removed_at=None).first() if wanted else None
    require(row is not None and str(wanted) == item_id, 'plan_item_not_found')
    return row


def describe(book, value):
    checked = plan_check(book, value)
    return {'books': book.entity.name, 'currency': book.currency, 'description': checked['description'], 'amount': money(checked['amount_units']),
            'date': checked['date'].isoformat(), 'every': checked['every'], 'until': checked['until'].isoformat() if checked['until'] else None,
            'scenario': checked['scenario'], 'current': True}


def describe_remove(book, item_id):
    row = _item(book, item_id)
    shown = _value(row)
    return {'books': book.entity.name, 'currency': book.currency, **{key: shown[key] for key in ('description', 'amount', 'date', 'every', 'until', 'scenario')}, 'current': True}


@guarded
@transaction.atomic
def add_item(user, book_id, value):
    """Save an approved planned item. The books do not change: no entry, no balance, no revision."""
    user, book = owned(user, book_id, True)
    require(book.created_by_id == user.pk, 'ledger_confirmation_denied')
    row = CashPlanItem.objects.create(book=book, created_by=user, **plan_check(book, value))
    return {'item_id': str(row.pk)}


@guarded
def remove_check(user, book_id, item_id):
    user, book = owned(user, book_id, True)
    require(book.created_by_id == user.pk, 'ledger_confirmation_denied')
    return _item(book, item_id)


@guarded
@transaction.atomic
def remove_item(user, book_id, item_id):
    row = remove_check(user, book_id, item_id)
    row.removed_at = timezone.now()
    row.save(update_fields=('removed_at',))
    return {'item_id': str(row.pk), 'removed': True}


def _dates(row, first, last):
    """The item's dates inside [first, last]: its own date, then every 7 days or the same day of each month (month ends clamped)."""
    end = min(last, row.until) if row.until else last
    if row.every is None:
        return [row.date] if first <= row.date <= end else []
    found, when, year, month = [], row.date, row.date.year, row.date.month
    while when <= end and len(found) < 120:
        if when >= first:
            found.append(when)
        if row.every == 'week':
            when += timedelta(days=7)
        else:
            year, month = year + (month == 12), month % 12 + 1
            following = date(year + (month == 12), month % 12 + 1, 1)
            when = min(date(year, month, 1) + timedelta(days=row.date.day - 1), following - timedelta(days=1))
    return found


@guarded
@transaction.atomic
def forecast(user, book_id, value):
    """The books-only 13-week forecast, plus the plan track and the list of planned items and scenarios. Read-only."""
    require(type(value) is dict and {'from'} <= set(value) <= {'from', 'scenario'})
    wanted = text(value['scenario'], 60) if 'scenario' in value else None
    result = cash_forecast(user, book_id, {'from': value['from']})
    _, book = owned(user, book_id)
    rows = sorted(book.plan_items.filter(removed_at=None), key=lambda row: (row.date, row.description, str(row.pk)))
    names = sorted({row.scenario for row in rows if row.scenario})
    require(wanted is None or wanted in names, 'plan_scenario_not_found')
    first, last = day(result['from']), day(result['through'])
    moves = sorted(((when, row.description, row.amount_units, row.scenario) for row in rows if row.scenario in (None, wanted)
                    for when in _dates(row, first, last)), key=lambda item: (item[0], item[1]))
    plan = None
    if moves:
        weeks, added, position = [], 0, 0
        for week in result['weeks']:
            mine = []
            while position < len(moves) and moves[position][0] <= day(week['end']):
                mine.append(moves[position]); position += 1
            added += sum(item[2] for item in mine)
            weeks.append({'week': week['week'], 'start': week['start'], 'end': week['end'],
                          'planned_in': money(sum(item[2] for item in mine if item[2] > 0)), 'planned_out': money(-sum(item[2] for item in mine if item[2] < 0)),
                          'closing': money(balance_units(week['closing']) + added),
                          'items': [{'date': when.isoformat(), 'description': description, 'amount': money(amount), 'scenario': scenario}
                                    for when, description, amount, scenario in mine[:100]]})
        lowest = min(weeks, key=lambda week: (balance_units(week['closing']), week['week']))
        plan = {'scenario': wanted, 'opening_usable': result['opening_usable'], 'weeks': weeks, 'closing_usable': weeks[-1]['closing'],
                'lowest': {'week': lowest['week'], 'closing': lowest['closing']}}
    assert len(result['weeks']) == FORECAST_WEEKS
    return {**result, 'plan': plan, 'plan_items': [_value(row) for row in rows],
            'scenarios': [{'name': name, 'items': sum(1 for row in rows if row.scenario == name)} for name in names]}
