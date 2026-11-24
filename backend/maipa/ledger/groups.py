"""Entity groups: books of one owner, in one currency, read together.

A group changes no book. The combined report puts each entity's own statements side by side with their sum.
Dealings between the entities are removed only for accounts the person approved (`group.eliminate`), in a separate
`consolidated` section, and the report is `eliminated` only when those accounts cancel out exactly.
"""
from decimal import Decimal
import uuid

from django.db import transaction

from .models import Account, EntityGroup, EntityGroupBook, EntityGroupElimination
from .services import LedgerError, _statements, current, day, fields, guarded, money, owned, require, text

MAX_BOOKS = 20
MAX_GROUPS = 50
MAX_ELIMINATIONS = 100


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
            'books': [{'book_id': str(book.pk), 'name': book.entity.name} for book in books],
            'eliminated_accounts': row.eliminations.count()}


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
def eliminate_check(user, value):
    """Accounts of member books to leave out of the consolidated totals. An empty list clears them. Read-only."""
    fields(value, ('group_id', 'account_ids'))
    row, books = _group(user, value['group_id'])
    owner = current(user)
    require(all(book.created_by_id == owner.pk for book in books), 'ledger_not_found')
    wanted = value['account_ids']
    require(type(wanted) is list and len(wanted) <= MAX_ELIMINATIONS and all(type(item) is str and str(uuid.UUID(item)) == item for item in wanted))
    require(len(set(wanted)) == len(wanted), 'group_account_repeated')
    found = {str(account.pk): account for account in Account.objects.filter(pk__in=wanted, book__in=books).select_related('book__entity')}
    require(len(found) == len(wanted), 'group_account_outside')
    order = {book.pk: position for position, book in enumerate(books)}
    return row, sorted(found.values(), key=lambda account: (order[account.book_id], account.code))


@guarded
def describe_eliminate(user, value):
    row, accounts = eliminate_check(user, value)
    return {'name': row.name, 'current': True, 'accounts': [{'business': account.book.entity.name, 'account': f'{account.code} {account.name}',
                                                              'category': account.category} for account in accounts]}


@guarded
@transaction.atomic
def set_eliminations(user, value):
    row, accounts = eliminate_check(user, value)
    row.eliminations.all().delete()
    EntityGroupElimination.objects.bulk_create([EntityGroupElimination(group=row, account=account) for account in accounts])
    return {'group_id': str(row.pk), 'accounts': len(accounts)}


def _consolidated(row, books, each, combined):
    """Totals without the approved accounts, each removed amount, and what fails to cancel out."""
    order = {book.pk: position for position, book in enumerate(books)}
    marked = sorted(row.eliminations.select_related('account__book__entity'), key=lambda item: (order[item.account.book_id], item.account.code))
    if not marked:
        return None
    sections = {'income': ('income_statement', 'income'), 'expense': ('income_statement', 'expenses'), 'asset': ('balance_sheet', 'assets'),
                'liability': ('balance_sheet', 'liabilities'), 'equity': ('balance_sheet', 'equity')}
    removed, by_category = [], dict.fromkeys(sections, 0)
    for item in marked:
        account = item.account
        part, key = sections[account.category]
        units = next((_units(found['amount']) for found in each[order[account.book_id]][part][key] if found['id'] == str(account.pk)), 0)
        by_category[account.category] += units
        removed.append({'book_id': str(account.book_id), 'business': account.book.entity.name, 'code': account.code, 'name': account.name,
                        'category': account.category, 'amount': money(units)})
    total = lambda part, key: _units(combined[part][key]['total'])
    income, expenses = total('income_statement', 'total_income') - by_category['income'], total('income_statement', 'total_expenses') - by_category['expense']
    return {'removed': removed, 'total_income': money(income), 'total_expenses': money(expenses), 'net_income': money(income - expenses),
            'total_assets': money(total('balance_sheet', 'total_assets') - by_category['asset']),
            'total_liabilities': money(total('balance_sheet', 'total_liabilities') - by_category['liability']),
            'total_equity': money(total('balance_sheet', 'total_equity') - by_category['equity']),
            'earnings_to_date': combined['balance_sheet']['earnings_to_date']['total'],
            # Both are zero when every dealing was recorded by both sides in the approved accounts.
            'income_difference': money(by_category['income'] - by_category['expense']),
            'balance_difference': money(by_category['asset'] - by_category['liability'] - by_category['equity'])}


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
    """Each member's statements for the range beside their sum, and the consolidated totals when accounts were approved for removal. Read-only."""
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
    result = {'group_id': str(row.pk), 'name': row.name, 'currency': books[0].currency, 'from': start.isoformat(), 'to': end.isoformat(),
            'books': [{'book_id': str(book.pk), 'name': book.entity.name, 'revision': book.revision} for book in books],
            'income_statement': {'income': rows(income('income')), 'expenses': rows(income('expenses')), 'total_income': total(income('total_income')),
                                 'total_expenses': total(income('total_expenses')), 'net_income': total(income('net_income'))},
            'balance_sheet': {'as_of': end.isoformat(), 'assets': rows(sheet('assets')), 'liabilities': rows(sheet('liabilities')), 'equity': rows(sheet('equity')),
                              'total_assets': total(sheet('total_assets')), 'total_liabilities': total(sheet('total_liabilities')),
                              'total_equity': total(sheet('total_equity')), 'earnings_to_date': total(sheet('earnings_to_date')),
                              'total_liabilities_and_equity': total(sheet('total_liabilities_and_equity'))},
            'cash': {'opening_cash': total(cash('opening_cash')), 'closing_cash': total(cash('closing_cash')), 'net_change': total(cash('net_change'))}}
    consolidated = _consolidated(row, books, each, result)
    cancels = consolidated is not None and consolidated['income_difference'] == consolidated['balance_difference'] == money(0)
    return {**result, 'eliminated': cancels, 'consolidated': consolidated}
