"""Entity groups: books of one owner, in one currency, read together.

A group changes no book. The combined report puts each entity's own statements side by side with their sum.
Balances between the entities are NOT eliminated here; `eliminated` says so on every report.
"""
from decimal import Decimal
import uuid

from django.db import transaction

from .models import EntityGroup, EntityGroupBook
from .services import LedgerError, _statements, current, day, fields, guarded, money, owned, require, text

MAX_BOOKS = 20
MAX_GROUPS = 50


def _units(value):
    return int(Decimal(value) * 10000)


@guarded
def group_check(user, value):
    """A named set of at least two different books the person owns, all in one currency. Read-only."""
    fields(value, ('name', 'book_ids'))
    name = text(value['name'], 120)
    require(type(value['book_ids']) is list and all(type(item) is str for item in value['book_ids']))
    require(2 <= len(value['book_ids']) <= MAX_BOOKS, 'group_needs_two_books' if len(value['book_ids']) < 2 else 'invalid_ledger_request')
    require(len(set(value['book_ids'])) == len(value['book_ids']), 'group_book_repeated')
    books = [owned(user, book_id, True)[1] for book_id in value['book_ids']]
    owner = current(user)
    require(all(book.created_by_id == owner.pk for book in books), 'ledger_not_found')
    require(len({book.currency for book in books}) == 1, 'group_currency_differs')
    wanted = {book.pk for book in books}
    for row in EntityGroup.objects.filter(owner=owner, removed_at=None).prefetch_related('members'):
        require({member.book_id for member in row.members.all()} != wanted, 'group_already_exists')
    require(EntityGroup.objects.filter(owner=owner, removed_at=None).count() < MAX_GROUPS, 'group_limit')
    return {'name': name, 'books': books, 'currency': books[0].currency}


def _group(user, group_id):
    owner = current(user)
    try:
        wanted = uuid.UUID(group_id) if type(group_id) is str else None
    except ValueError:
        wanted = None
    row = EntityGroup.objects.filter(pk=wanted, owner=owner, removed_at=None).first() if wanted else None
    require(row is not None and str(wanted) == group_id, 'group_not_found')
    books = [member.book for member in row.members.select_related('book__entity').order_by('position')]
    for book in books:        # Access to every member is rechecked on each read.
        owned(user, str(book.pk))
    return row, books


def _value(row, books):
    return {'id': str(row.pk), 'name': row.name, 'currency': books[0].currency,
            'books': [{'book_id': str(book.pk), 'name': book.entity.name} for book in books]}


@guarded
def describe(user, value):
    checked = group_check(user, value)
    return {'name': checked['name'], 'currency': checked['currency'], 'books': [book.entity.name for book in checked['books']], 'current': True}


@guarded
def describe_remove(user, group_id):
    row, books = _group(user, group_id)
    return {'name': row.name, 'currency': books[0].currency, 'books': [book.entity.name for book in books], 'current': True}


@guarded
@transaction.atomic
def create_group(user, value):
    checked = group_check(user, value)
    row = EntityGroup.objects.create(owner=current(user), name=checked['name'])
    EntityGroupBook.objects.bulk_create([EntityGroupBook(group=row, book=book, position=position) for position, book in enumerate(checked['books'])])
    return {'group_id': str(row.pk)}


@guarded
def remove_check(user, group_id):
    return _group(user, group_id)[0]


@guarded
@transaction.atomic
def remove_group(user, group_id):
    from django.utils import timezone
    row = remove_check(user, group_id)
    row.removed_at = timezone.now()
    row.save(update_fields=('removed_at',))
    return {'group_id': str(row.pk), 'removed': True}


@guarded
@transaction.atomic
def list_groups(user):
    owner = current(user)
    result = []
    for row in EntityGroup.objects.filter(owner=owner, removed_at=None).order_by('created_at', 'id')[:MAX_GROUPS]:
        try:
            result.append(_value(*_group(user, str(row.pk))))
        except LedgerError:
            continue        # A member the person can no longer read hides the group rather than part of it.
    return {'groups': result}


@guarded
@transaction.atomic
def combined_report(user, group_id, value):
    """Each member's statements for the range beside their sum. Read-only; nothing between the entities is eliminated."""
    row, books = _group(user, group_id)
    fields(value, ('from', 'to'))
    start, end = day(value['from']), day(value['to'])
    each = [_statements(book, {a.pk: a for a in book.accounts.all()}, start, end)[0] for book in books]
    count = len(books)

    def total(read):
        units = [_units(read(statements)) for statements in each]
        return {'amounts': [money(item) for item in units], 'total': money(sum(units))}

    def rows(read):
        merged = {}        # Accounts combine only when both their code and their name agree.
        for position, statements in enumerate(each):
            for item in read(statements):
                merged.setdefault((item['code'], item['name']), [0] * count)[position] += _units(item['amount'])
        return [{'code': code, 'name': name, 'amounts': [money(item) for item in units], 'total': money(sum(units))}
                for (code, name), units in sorted(merged.items())]
    income, sheet, cash = (lambda key: (lambda s: s['income_statement'][key])), (lambda key: (lambda s: s['balance_sheet'][key])), (lambda key: (lambda s: s['cash_flow'][key]))
    return {'group_id': str(row.pk), 'name': row.name, 'currency': books[0].currency, 'from': start.isoformat(), 'to': end.isoformat(), 'eliminated': False,
            'books': [{'book_id': str(book.pk), 'name': book.entity.name, 'revision': book.revision} for book in books],
            'income_statement': {'income': rows(income('income')), 'expenses': rows(income('expenses')), 'total_income': total(income('total_income')),
                                 'total_expenses': total(income('total_expenses')), 'net_income': total(income('net_income'))},
            'balance_sheet': {'as_of': end.isoformat(), 'assets': rows(sheet('assets')), 'liabilities': rows(sheet('liabilities')), 'equity': rows(sheet('equity')),
                              'total_assets': total(sheet('total_assets')), 'total_liabilities': total(sheet('total_liabilities')),
                              'total_equity': total(sheet('total_equity')), 'earnings_to_date': total(sheet('earnings_to_date')),
                              'total_liabilities_and_equity': total(sheet('total_liabilities_and_equity'))},
            'cash': {'opening_cash': total(cash('opening_cash')), 'closing_cash': total(cash('closing_cash')), 'net_change': total(cash('net_change'))}}
