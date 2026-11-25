"""Native books: exact four-decimal integer storage and immutable entry proposals.

Proposals have no posting side effect. Posting requires a separate exact-payload
human confirmation under fresh authority; it is never inferred from a draft.
"""
from datetime import date, timedelta
from decimal import Decimal
from functools import wraps
import csv
import hashlib
import io
import json
import re
import uuid

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from backend.maipa.intake import services as intake
from .models import (Account, Book, CATEGORIES, Journal, JournalLine, LedgerEvent, PostingReceipt, Reconciliation, RecurringEntry, ReportRelease, SourceImport, SourceRow,
                     SourceRowMatch)

MAX_UNITS = 999999999999999999
STARTER = (('1000', 'Checking', 'asset'), ('1100', 'Accounts receivable', 'asset'),
           ('1500', 'Equipment', 'asset'), ('2000', 'Accounts payable', 'liability'),
           ('2100', 'Sales tax payable', 'liability'), ('3000', 'Owner equity', 'equity'),
           ('3900', 'Opening balance equity', 'equity'), ('4000', 'Sales', 'income'),
           ('5000', 'Cost of sales', 'expense'), ('6000', 'General expenses', 'expense'),
           ('6100', 'Bank fees', 'expense'), ('6200', 'Office expenses', 'expense'))
STARTER_BANK = '1000'  # The starter account that is a bank account.


class LedgerError(Exception):
    def __init__(self, code='ledger_unavailable'):
        self.code = code
        super().__init__(code)


def require(condition, code='invalid_ledger_request'):
    if not condition:
        raise LedgerError(code)


def guarded(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (intake.ScopeDenied, intake.NotFound, get_user_model().DoesNotExist):
            raise LedgerError('ledger_not_found') from None
        except (ValueError, TypeError, AttributeError, KeyError, OverflowError, IntegrityError):
            raise LedgerError('invalid_ledger_request') from None
    return call


def fields(value, keys):
    require(type(value) is dict and set(value) == set(keys))


def identity(value):
    require(type(value) is str)
    parsed = uuid.UUID(value)
    require(str(parsed) == value)
    return parsed


def day(value):
    require(type(value) is str and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value) is not None)
    return date.fromisoformat(value)


def text(value, maximum):
    require(type(value) is str and 0 < len(value) <= maximum and value == value.strip()
            and not any(ord(char) < 32 or ord(char) == 127 for char in value))
    return value


def units(value):
    require(type(value) is str and re.fullmatch(r'(?:0|[1-9][0-9]{0,13})(?:\.[0-9]{1,4})?', value) is not None)
    return int(Decimal(value) * 10000)


def money(value):
    return ('-' if value < 0 else '') + f'{abs(value) // 10000}.{abs(value) % 10000:04d}'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def current(user):
    require(getattr(user, 'is_authenticated', False) and user.pk is not None, 'ledger_not_found')
    return get_user_model().objects.get(pk=user.pk, is_active=True)


def owned(user, book_id, write=False):
    user = current(user)
    row = Book.objects.select_related('entity__engagement').filter(pk=identity(book_id)).first()
    require(row is not None, 'ledger_not_found')
    intake.owned_entity(user, row.entity_id, write)
    return user, row


def account_value(row):
    return {'id': str(row.pk), 'code': row.code, 'name': row.name, 'category': row.category, 'cash': row.cash}


def entry_value(row):
    return {'id': str(row.pk), 'book_id': str(row.book_id), 'sequence': row.sequence,
            'operation_id': str(row.operation_id), 'payload_hash': row.payload_hash,
            'state': row.state, 'date': row.date.isoformat(), 'description': row.description,
            'source': {'kind': row.source_kind, 'reference': row.source_reference},
            'evidence': row.evidence, 'debit': money(row.total_units), 'credit': money(row.total_units),
            'lines': [{'account_id': str(line.account_id), 'debit': money(line.debit_units),
                       'credit': money(line.credit_units)} for line in row.lines.order_by('position')]}


def snapshot(row):
    page = list(row.journals.exclude(state='voided').order_by('-sequence')[:101])
    return {'id': str(row.pk), 'entity_id': str(row.entity_id), 'currency': row.currency,
            'period_start': row.period_start.isoformat(), 'period_end': row.period_end.isoformat(),
            'closed_through': row.closed_through.isoformat() if row.closed_through else None,
            'revision': row.revision, 'accounts': [account_value(a) for a in row.accounts.order_by('code')],
            'journals': [entry_value(entry) for entry in page[:100]], 'has_more_journals': len(page) > 100}


def advance(book, expected):
    require(type(expected) is int and 0 <= expected < 1_999_999_999)
    require(Book.objects.filter(pk=book.pk, revision=expected).update(revision=F('revision')+1) == 1,
            'ledger_revision_conflict')
    book.revision = expected + 1


def event(book, user, action, target, payload_hash):
    LedgerEvent.objects.create(book=book, revision=book.revision, actor=user, action=action,
                               target=target, payload_hash=payload_hash)


@guarded
@transaction.atomic
def create_book(user, entity_id, value):
    user = current(user)
    entity = intake.owned_entity(user, entity_id, True)
    fields(value, ('currency', 'period_start', 'period_end'))
    require(type(value['currency']) is str and re.fullmatch(r'[A-Z]{3}', value['currency']) is not None)
    start, end = day(value['period_start']), day(value['period_end'])
    require(start <= end)
    type(entity).objects.filter(pk=entity.pk).update(revision=F('revision'))
    row = Book.objects.filter(entity=entity).first()
    if row:
        require((row.currency, row.period_start, row.period_end) == (value['currency'], start, end),
                'book_already_initialized')
        return snapshot(row)
    row = Book.objects.create(entity=entity, currency=value['currency'], period_start=start,
                              period_end=end, created_by=user)
    Account.objects.bulk_create([Account(book=row, code=code, name=name, category=category, cash=code == STARTER_BANK)
                                 for code, name, category in STARTER])
    return snapshot(row)


@guarded
@transaction.atomic
def read_book(user, book_id):
    _, row = owned(user, book_id)
    return snapshot(row)


@guarded
@transaction.atomic
def create_account(user, book_id, value):
    user, row = owned(user, book_id, True)
    fields(value, ('expected_revision', 'code', 'name', 'category'))
    code = text(value['code'], 16); name = text(value['name'], 128)
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,15}', code) is not None)
    require(value['category'] in CATEGORIES)
    require(row.accounts.count() < 1000, 'ledger_account_limit')
    require(not row.accounts.filter(code=code).exists(), 'ledger_account_exists')
    advance(row, value['expected_revision'])
    account = Account.objects.create(book=row, code=code, name=name, category=value['category'])
    event(row, user, 'account.create', account.pk, digest(account_value(account)))
    return account_value(account)


def cash_change(book, account_id, cash):
    """The asset account whose bank-or-cash mark would change. Read-only."""
    require(type(cash) is bool)
    account = book.accounts.filter(pk=identity(account_id)).first()
    require(account is not None, 'ledger_account_not_found')
    require(account.category == 'asset', 'ledger_cash_account_invalid')
    require(account.cash is not cash, 'ledger_cash_unchanged')
    require(cash or not account.restricted, 'ledger_account_restricted')
    return account


def restricted_change(book, account_id, restricted):
    """The bank or cash account whose set-aside mark would change. Read-only."""
    require(type(restricted) is bool)
    account = book.accounts.filter(pk=identity(account_id)).first()
    require(account is not None, 'ledger_account_not_found')
    require(account.cash, 'ledger_restricted_needs_cash')
    require(account.restricted is not restricted, 'ledger_restricted_unchanged')
    return account


@guarded
@transaction.atomic
def set_account_restricted(user, book_id, account_id, value):
    """Set a bank or cash account aside as restricted, or release it. No amounts change."""
    user, book = owned(user, book_id, True)
    require(book.created_by_id == user.pk, 'ledger_confirmation_denied')
    fields(value, ('expected_revision', 'restricted'))
    account = restricted_change(book, account_id, value['restricted'])
    advance(book, value['expected_revision'])
    Account.objects.filter(pk=account.pk).update(restricted=value['restricted'])
    event(book, user, 'account.restricted', account.pk, digest({'account': str(account.pk), 'restricted': value['restricted']}))
    return {'account_id': str(account.pk), 'restricted': value['restricted'], 'revision': book.revision}


FORECAST_WEEKS = 13


@guarded
@transaction.atomic
def cash_forecast(user, book_id, value):
    """Usable cash for the 13 weeks from a date, from what the books already hold and nothing else. Read-only.

    Usable cash is the balance of bank and cash accounts not set aside as restricted. Opening is their posted balance
    before the start date. Each week then carries the cash effect of posted entries dated in it, of drafts awaiting
    approval, and of recurring entries not yet drafted. Anything of those dated before the start and still unposted
    is late and lands in the first week. Sales not yet invoiced, bills not yet entered and plans are not in the books,
    so they are not in the forecast.
    """
    _, book = owned(user, book_id)
    fields(value, ('from',))
    start = day(value['from'])
    require(book.period_start <= start <= book.period_end, 'ledger_date_outside_period')
    through = start + timedelta(days=7 * FORECAST_WEEKS - 1)
    cash = sorted(book.accounts.filter(cash=True), key=lambda account: account.code)
    usable = {account.pk for account in cash if not account.restricted}
    balances = dict.fromkeys((account.pk for account in cash), 0)
    items = []        # (date, description, units, source) for usable cash only
    for journal in book.journals.filter(state__in=('posted', 'draft')).prefetch_related('lines').order_by('date', 'sequence'):
        moved = 0
        for line in journal.lines.all():
            if line.account_id in balances:
                net = line.debit_units - line.credit_units
                if journal.state == 'posted' and journal.date < start:
                    balances[line.account_id] += net
                elif line.account_id in usable:
                    moved += net
        if moved and (journal.state == 'draft' or start <= journal.date <= through) and journal.date <= through:
            items.append((journal.date, journal.description, moved, journal.state))
    made = _made(book)
    for row in book.schedules.order_by('created_at')[:MAX_SCHEDULES * 4]:
        moved = sum(units(line['debit']) - units(line['credit']) for line in row.lines if uuid.UUID(line['account_id']) in usable)
        if moved:
            items.extend((when, row.description, moved, 'recurring') for when in _due(book, row, min(through, book.period_end), made))
    opening = sum(balances[pk] for pk in usable)
    weeks, running, position = [], opening, 0
    items.sort(key=lambda item: (item[0], item[1]))
    for number in range(1, FORECAST_WEEKS + 1):
        first, last = start + timedelta(days=7 * (number - 1)), start + timedelta(days=7 * number - 1)
        mine = []
        while position < len(items) and items[position][0] <= last:
            mine.append(items[position]); position += 1
        received, paid = sum(item[2] for item in mine if item[2] > 0), -sum(item[2] for item in mine if item[2] < 0)
        running += received - paid
        weeks.append({'week': number, 'start': first.isoformat(), 'end': last.isoformat(), 'in': money(received), 'out': money(paid),
                      'net': money(received - paid), 'closing': money(running),
                      'items': [{'date': when.isoformat(), 'description': description, 'amount': money(moved), 'source': source}
                                for when, description, moved, source in mine[:100]]})
    lowest = min(weeks, key=lambda week: (balance_units(week['closing']), week['week']))
    return {'book_id': str(book.pk), 'currency': book.currency, 'revision': book.revision, 'from': start.isoformat(), 'through': through.isoformat(),
            'period_end': book.period_end.isoformat(),
            'cash_accounts': [{'id': str(account.pk), 'code': account.code, 'name': account.name, 'restricted': account.restricted,
                               'balance': money(balances[account.pk])} for account in cash],
            'opening_usable': money(opening), 'restricted_cash': money(sum(balances[account.pk] for account in cash if account.restricted)),
            'weeks': weeks, 'closing_usable': money(running), 'lowest': {'week': lowest['week'], 'closing': lowest['closing']},
            'sources': {source: sum(1 for item in items if item[3] == source) for source in ('posted', 'draft', 'recurring')}}


@guarded
@transaction.atomic
def set_account_cash(user, book_id, account_id, value):
    """Mark or unmark an asset account as a bank account or cash on hand. No amounts change."""
    user, book = owned(user, book_id, True)
    require(book.created_by_id == user.pk, 'ledger_confirmation_denied')
    fields(value, ('expected_revision', 'cash'))
    account = cash_change(book, account_id, value['cash'])
    advance(book, value['expected_revision'])
    Account.objects.filter(pk=account.pk).update(cash=value['cash'])
    account.cash = value['cash']
    event(book, user, 'account.cash', account.pk, digest(account_value(account)))
    return account_value(account)


@guarded
@transaction.atomic
def propose_journal(user, book_id, value, _kind=None):
    user, book = owned(user, book_id, True)
    fields(value, ('operation_id', 'expected_revision', 'date', 'description', 'source', 'evidence', 'lines'))
    operation = identity(value['operation_id'])
    request_hash = digest(value)
    existing = book.journals.filter(operation_id=operation).first()
    if existing:
        require(existing.request_hash == request_hash and existing.state != 'voided', 'ledger_operation_conflict')
        return entry_value(existing)
    dated = day(value['date'])
    require(book.period_start <= dated <= book.period_end, 'ledger_date_outside_period')
    require(not closed(book, dated), 'ledger_period_closed')
    description = text(value['description'], 500)
    fields(value['source'], ('kind', 'reference'))
    # Reversal, opening and recurring entries are only ever built by their own services.
    require(value['source']['kind'] in ('manual', 'csv') or (_kind in ('reversal', 'opening', 'schedule', 'accrual', 'bill', 'billpay') and value['source']['kind'] == _kind))
    reference = text(value['source']['reference'], 256)
    require(type(value['evidence']) is list and 1 <= len(value['evidence']) <= 20)
    for item in value['evidence']:
        text(item, 256)
    require(type(value['lines']) is list and 2 <= len(value['lines']) <= 100)
    lines, debit, credit = [], 0, 0
    for position, line in enumerate(value['lines']):
        fields(line, ('account_id', 'debit', 'credit'))
        account = book.accounts.filter(pk=identity(line['account_id'])).first()
        require(account is not None, 'ledger_account_not_found')
        left, right = units(line['debit']), units(line['credit'])
        require((left > 0 and right == 0) or (right > 0 and left == 0))
        debit += left; credit += right
        lines.append((account, position, left, right))
    require(0 < debit == credit <= MAX_UNITS, 'ledger_unbalanced')
    if value['source']['kind'] == 'csv':
        try:
            row_id = identity(reference)
        except (LedgerError, ValueError):
            raise LedgerError('import_row_not_found') from None
        row = SourceRow.objects.filter(pk=row_id, source_import__book=book).select_related('source_import').first()
        require(row is not None, 'import_row_not_found')
        require(row.date == dated, 'import_date_mismatch')
        bank = row.source_import.account_id
        # Without a bank account the entry's total must be the line amount. With one, the bank account's own
        # movement must be the line amount, so a payout can carry its gross sale and fees in the same entry.
        require(bank is not None or abs(row.amount_units) == debit, 'import_amount_mismatch')
        if bank is not None:
            # Money in debits the statement's bank account; money out credits it; never both.
            moved_in = sum(left for account, _, left, _ in lines if account.pk == bank)
            moved_out = sum(right for account, _, _, right in lines if account.pk == bank)
            require((moved_in, moved_out) == ((row.amount_units, 0) if row.amount_units > 0 else (0, -row.amount_units)),
                    'import_account_mismatch')
    require(not book.journals.exclude(state='voided').filter(source_kind=value['source']['kind'], source_reference=reference).exists(),
            'ledger_source_exists')
    # A line already settled by a match cannot also get an entry of its own.
    require(value['source']['kind'] != 'csv' or (str(row_id) not in _settled(book) and str(row_id) not in _duplicates(book)),
            'ledger_source_exists')
    payload = {'book_id': str(book.pk), 'entity_id': str(book.entity_id), 'currency': book.currency,
               'date': value['date'], 'description': description, 'source': value['source'],
               'evidence': value['evidence'], 'lines': [{'account_id': str(a.pk), 'debit': money(d),
                    'credit': money(c)} for a, _, d, c in lines]}
    advance(book, value['expected_revision'])
    journal = Journal.objects.create(book=book, operation_id=operation, sequence=book.revision,
        request_hash=request_hash, payload_hash=digest(payload), date=dated, description=description,
        source_kind=value['source']['kind'], source_reference=reference, evidence=value['evidence'],
        total_units=debit, proposed_by=user)
    JournalLine.objects.bulk_create([JournalLine(journal=journal, account=account, position=position,
        debit_units=left, credit_units=right) for account, position, left, right in lines])
    event(book, user, 'journal.propose', journal.pk, journal.payload_hash)
    return entry_value(journal)


@guarded
@transaction.atomic
def journal_preview(user, book_id):
    _, book = owned(user, book_id)
    values = {a.pk: {**account_value(a), 'draft': [0, 0], 'posted': [0, 0]} for a in book.accounts.order_by('code')}
    # Python integers avoid SQLite SUM overflow and floating aggregate coercion.
    for line in JournalLine.objects.filter(journal__book=book).exclude(journal__state='voided').select_related('journal').iterator(chunk_size=1000):
        amounts = values[line.account_id][line.journal.state]
        amounts[0] += line.debit_units; amounts[1] += line.credit_units
    result = []
    posted_debit = posted_credit = trial_debit = trial_credit = 0
    for item in values.values():
        draft, posted = item.pop('draft'), item.pop('posted')
        posted_debit += posted[0]; posted_credit += posted[1]
        net = posted[0] - posted[1]
        trial_debit += max(net, 0); trial_credit += max(-net, 0)
        result.append({**item, 'draft_debit': money(draft[0]), 'draft_credit': money(draft[1]),
                       'posted_debit': money(posted[0]), 'posted_credit': money(posted[1]),
                       'balance_debit': money(max(net, 0)), 'balance_credit': money(max(-net, 0))})
    return {'book_id': str(book.pk), 'currency': book.currency, 'revision': book.revision,
            'accounts': result, 'posted_debit': money(posted_debit), 'posted_credit': money(posted_credit),
            'trial_debit': money(trial_debit), 'trial_credit': money(trial_credit),
            'pending_count': book.journals.filter(state='draft').count()}


MAX_IMPORT_BYTES = 524_288
MAX_IMPORT_ROWS = 1000
IMPORT_HEADERS = (['date', 'description', 'amount'], ['date', 'description', 'amount', 'reference'])


def signed_units(value):
    """A signed statement amount with at most four decimals; never zero."""
    require(type(value) is str and re.fullmatch(r'-?(?:0|[1-9][0-9]{0,13})(?:\.[0-9]{1,4})?', value) is not None)
    result = int(Decimal(value) * 10000)
    require(result != 0 and abs(result) <= MAX_UNITS)
    return result


def parse_statement(book, content):
    """Every row must be valid or the whole file is refused, naming the first bad row."""
    require(type(content) is str and 0 < len(content.encode('utf-8')) <= MAX_IMPORT_BYTES, 'import_file_invalid')
    lines = list(csv.reader(io.StringIO(content, newline=''), strict=True))
    require(len(lines) >= 2 and [cell.strip().lower() for cell in lines[0]] in IMPORT_HEADERS, 'import_header_invalid')
    width = len(lines[0])
    body = [line for line in lines[1:] if line]
    require(0 < len(body) <= MAX_IMPORT_ROWS, 'import_row_count_invalid')
    rows = []
    for number, line in enumerate(body, start=1):
        try:
            require(len(line) == width)
            cells = [cell.strip() for cell in line]
            dated = day(cells[0])
            require(book.period_start <= dated <= book.period_end)
            reference = cells[3] if width == 4 else ''
            if reference:
                text(reference, 128)
            rows.append(SourceRow(row_number=number, date=dated, description=text(cells[1], 500),
                                  amount_units=signed_units(cells[2]), reference=reference))
        except (LedgerError, ValueError):
            raise LedgerError(f'import_row_{number}_invalid') from None
    return rows


def import_value(row, created):
    return {'id': str(row.pk), 'book_id': str(row.book_id), 'filename': row.filename, 'sha256': row.sha256,
            'row_count': row.row_count, 'account_id': str(row.account_id) if row.account_id else None,
            'opening_balance': money(row.opening_units) if row.opening_units is not None else None,
            'closing_balance': money(row.closing_units) if row.closing_units is not None else None,
            'created': created, 'created_at': row.created_at.isoformat()}


def balance_units(value):
    """A stated balance: signed, possibly zero, at most four decimals."""
    require(type(value) is str and re.fullmatch(r'-?(?:0|[1-9][0-9]{0,13})(?:\.[0-9]{1,4})?', value) is not None)
    result = int(Decimal(value) * 10000)
    require(abs(result) <= MAX_UNITS)
    return result


def statement_rows(book, value):
    """The parsed rows and stated balances of a statement. With balances, the rows must add up from one to the other."""
    stated = [key for key in ('opening_balance', 'closing_balance') if key in value]
    require(len(stated) in (0, 2))
    rows = parse_statement(book, value['content'])
    if not stated:
        return rows, None, None
    opening, closing = balance_units(value['opening_balance']), balance_units(value['closing_balance'])
    require(opening + sum(row.amount_units for row in rows) == closing, 'import_balance_mismatch')
    return rows, opening, closing


def import_account(book, account_id):
    """The marked bank or cash account of this book that a statement names. Read-only."""
    try:
        account = book.accounts.filter(pk=identity(account_id)).first()
    except (LedgerError, ValueError):
        account = None
    require(account is not None and account.cash, 'import_account_invalid')
    return account


@guarded
@transaction.atomic
def import_statement(user, book_id, value):
    """Save a CSV statement's rows as pending source records; no journal is created."""
    user, book = owned(user, book_id, True)
    require(type(value) is dict)
    fields(value, ('expected_revision', 'filename', 'content',
                   *(key for key in ('account_id', 'opening_balance', 'closing_balance') if key in value)))
    account = import_account(book, value['account_id']) if 'account_id' in value else None
    filename = text(value['filename'], 128)
    require(re.fullmatch(r'[^/\\:*?"<>|]+', filename) is not None and filename not in ('.', '..'), 'import_file_invalid')
    require(type(value['content']) is str, 'import_file_invalid')
    sha = hashlib.sha256(value['content'].encode('utf-8')).hexdigest()
    existing = book.imports.filter(sha256=sha).first()
    if existing:
        return import_value(existing, False)
    rows, opening, closing = statement_rows(book, value)
    advance(book, value['expected_revision'])
    saved = SourceImport.objects.create(book=book, sha256=sha, filename=filename, row_count=len(rows), account=account,
                                        opening_units=opening, closing_units=closing, imported_by=user)
    for row in rows:
        row.source_import = saved
    SourceRow.objects.bulk_create(rows)
    event(book, user, 'statement.import', saved.pk, sha)
    return import_value(saved, True)


def _statement(book, import_id):
    try:
        statement = book.imports.filter(pk=identity(import_id)).select_related('account').first()
    except (LedgerError, ValueError):
        statement = None
    require(statement is not None, 'import_not_found')
    return statement


def _reconcile(book, statement):
    """The reconciliation of one statement, in exact units, with the listed items."""
    account = statement.account
    require(account is not None, 'match_account_required')
    require(statement.closing_units is not None and statement.opening_units is not None, 'reconciliation_balances_required')
    dates = sorted(statement.rows.values_list('date', flat=True))
    as_of = dates[-1]
    settled, repeats, undone = _settled(book), _duplicates(book), _reversed(book)
    # Entries that a statement line of this account has cleared, when both the line and the posted entry fall on or before the date.
    cleared, unrecorded = set(), []
    for row in SourceRow.objects.filter(source_import__book=book, source_import__account=account, date__lte=as_of).order_by(
            'date', 'source_import__created_at', 'row_number')[:MAX_IMPORT_ROWS * 5]:
        if str(row.pk) in repeats:
            continue
        journal = settled.get(str(row.pk), (None, None))[0]
        if journal is not None and journal.state == 'posted' and journal.date <= as_of:
            cleared.add(journal.pk)
        else:
            unrecorded.append({'row_id': str(row.pk), 'date': row.date.isoformat(), 'description': row.description,
                               'amount': money(row.amount_units), 'status': 'pending' if journal is None else
                               'proposed' if journal.state != 'posted' else 'posted_later', 'units': row.amount_units})
    balance, moves = 0, {}
    for line in JournalLine.objects.filter(journal__book=book, journal__state='posted', journal__date__lte=as_of,
                                           account=account).select_related('journal'):
        net = line.debit_units - line.credit_units
        balance += net
        moves[line.journal_id] = (line.journal, moves.get(line.journal_id, (None, 0))[1] + net)
    outstanding = []
    for journal, net in sorted(moves.values(), key=lambda item: item[0].sequence):
        # Opening entries are the bank's own starting balance; an entry and its reversal cancel out.
        if (journal.pk in cleared or journal.source_kind in ('opening', 'reversal') or str(journal.pk) in undone or net == 0):
            continue
        outstanding.append({'journal_id': str(journal.pk), 'sequence': journal.sequence, 'date': journal.date.isoformat(),
                            'description': journal.description, 'amount': money(net), 'units': net})
    unrecorded_units = sum(item.pop('units') for item in unrecorded)
    outstanding_units = sum(item.pop('units') for item in outstanding)
    figures = digest({'closing': statement.closing_units, 'book': balance, 'unrecorded': [item['row_id'] for item in unrecorded],
                      'outstanding': [item['journal_id'] for item in outstanding]})
    return {'account': account, 'first': dates[0], 'as_of': as_of, 'balance': balance, 'unrecorded': unrecorded, 'outstanding': outstanding,
            'unrecorded_units': unrecorded_units, 'outstanding_units': outstanding_units, 'figures': figures,
            'adjusted_books': balance + unrecorded_units, 'adjusted_bank': statement.closing_units + outstanding_units}


def _recorded(book, statement, figures=None):
    """The recorded reconciliation of a statement, and whether its figures are unchanged today."""
    saved = Reconciliation.objects.filter(statement=statement).first()
    if saved is None:
        return None
    if figures is None:
        figures = _reconcile(book, statement)['figures']
    return {'recorded_at': saved.created_at.isoformat(), 'as_of': saved.as_of.isoformat(), 'unchanged': saved.figures_hash == figures}


@guarded
@transaction.atomic
def statements(user, book_id):
    """Every imported statement with its bank account, stated balances and dates, whether it can be reconciled, and any recorded reconciliation."""
    _, book = owned(user, book_id)
    listed = []
    for statement in book.imports.select_related('account').order_by('created_at')[:500]:
        dates = sorted(statement.rows.values_list('date', flat=True))
        account = statement.account
        able = account is not None and statement.closing_units is not None
        listed.append({'id': str(statement.pk), 'filename': statement.filename, 'account_id': str(account.pk) if account else None,
                       'account': f'{account.code} {account.name}' if account else None,
                       'opening_balance': money(statement.opening_units) if statement.opening_units is not None else None,
                       'closing_balance': money(statement.closing_units) if statement.closing_units is not None else None,
                       'first_date': dates[0].isoformat(), 'last_date': dates[-1].isoformat(), 'row_count': statement.row_count,
                       'can_reconcile': able, 'reconciliation': _recorded(book, statement) if able else None})
    return {'book_id': str(book.pk), 'revision': book.revision, 'statements': listed}


def _report(book, statement):
    found = _reconcile(book, statement)
    account = found['account']
    return {'book_id': str(book.pk), 'currency': book.currency, 'revision': book.revision,
            'statement': {'id': str(statement.pk), 'filename': statement.filename,
                          'account': {'id': str(account.pk), 'code': account.code, 'name': account.name},
                          'opening_balance': money(statement.opening_units), 'closing_balance': money(statement.closing_units),
                          'first_date': found['first'].isoformat(), 'as_of': found['as_of'].isoformat()},
            'book_balance': money(found['balance']), 'unrecorded_lines': found['unrecorded'][:500],
            'unrecorded_total': money(found['unrecorded_units']), 'outstanding_entries': found['outstanding'][:500],
            'outstanding_total': money(found['outstanding_units']), 'adjusted_books': money(found['adjusted_books']),
            'adjusted_bank': money(found['adjusted_bank']), 'difference': money(found['adjusted_books'] - found['adjusted_bank']),
            'reconciled': found['adjusted_books'] == found['adjusted_bank'], 'recorded': _recorded(book, statement, found['figures'])}


@guarded
@transaction.atomic
def reconciliation(user, book_id, value):
    """One statement's closing balance against the books at its last date. Read-only; nothing is adjusted.

    Books plus statement lines not yet posted must equal the bank's closing balance plus posted entries
    the bank has not shown. Whatever remains is the unexplained difference.
    """
    _, book = owned(user, book_id)
    fields(value, ('import_id',))
    return _report(book, _statement(book, value['import_id']))


def record_check(book, import_id):
    """A statement that agrees with the books today and has no recorded reconciliation yet. Read-only."""
    statement = _statement(book, import_id)
    found = _reconcile(book, statement)
    require(not Reconciliation.objects.filter(statement=statement).exists(), 'reconciliation_already_recorded')
    require(found['adjusted_books'] == found['adjusted_bank'], 'reconciliation_difference')
    return statement, found


@guarded
@transaction.atomic
def record_reconciliation(user, book_id, value):
    """Record that the person reviewed this statement and it agrees with the books. No entries or amounts change."""
    user, book = owned(user, book_id, True)
    require(book.created_by_id == user.pk, 'ledger_confirmation_denied')
    fields(value, ('expected_revision', 'import_id'))
    statement, found = record_check(book, value['import_id'])
    advance(book, value['expected_revision'])
    Reconciliation.objects.create(statement=statement, account=found['account'], as_of=found['as_of'], closing_units=statement.closing_units,
                                  book_units=found['balance'], unrecorded_units=found['unrecorded_units'],
                                  outstanding_units=found['outstanding_units'], figures_hash=found['figures'], recorded_by=user)
    event(book, user, 'reconciliation.record', statement.pk, found['figures'])
    return {'import_id': str(statement.pk), 'as_of': found['as_of'].isoformat(), 'closing_balance': money(statement.closing_units),
            'book_balance': money(found['balance']), 'revision': book.revision}


def balances_check(book, import_id, opening, closing):
    """A statement without stated balances, and balances its rows add up between. Read-only."""
    statement = _statement(book, import_id)
    require(statement.opening_units is None and statement.closing_units is None, 'import_balances_already_set')
    opening_units, closing_units = balance_units(opening), balance_units(closing)
    require(opening_units + sum(statement.rows.values_list('amount_units', flat=True)) == closing_units, 'import_balance_mismatch')
    return statement, opening_units, closing_units


@guarded
@transaction.atomic
def set_statement_balances(user, book_id, value):
    """Add the stated opening and closing balance to an earlier statement. No entries or amounts change."""
    user, book = owned(user, book_id, True)
    require(book.created_by_id == user.pk, 'ledger_confirmation_denied')
    fields(value, ('expected_revision', 'import_id', 'opening_balance', 'closing_balance'))
    statement, opening, closing = balances_check(book, value['import_id'], value['opening_balance'], value['closing_balance'])
    advance(book, value['expected_revision'])
    require(SourceImport.objects.filter(pk=statement.pk, opening_units__isnull=True, closing_units__isnull=True).update(
        opening_units=opening, closing_units=closing) == 1, 'import_balances_already_set')
    statement.opening_units, statement.closing_units = opening, closing
    event(book, user, 'statement.balances', statement.pk, digest({'opening': opening, 'closing': closing}))
    return import_value(statement, False)


MATCH_DAYS = 10


def statement_account_check(book, import_id, account_id):
    """A statement not yet tied to a bank account, and the account its existing entries already agree with. Read-only."""
    try:
        statement = book.imports.filter(pk=identity(import_id)).first()
    except (LedgerError, ValueError):
        statement = None
    require(statement is not None, 'import_not_found')
    require(statement.account_id is None, 'import_account_already_set')
    account = import_account(book, account_id)
    settled = _settled(book)
    for row in statement.rows.all():
        journal = settled.get(str(row.pk), (None, None))[0]
        # Every entry that still settles one of its lines must already move this account by the line amount.
        require(journal is None or _moves(list(journal.lines.all()), account.pk, row.amount_units), 'import_account_mismatch')
    return statement, account


@guarded
@transaction.atomic
def set_statement_account(user, book_id, value):
    """Tie an earlier statement to its bank or cash account. No entries or amounts change."""
    user, book = owned(user, book_id, True)
    require(book.created_by_id == user.pk, 'ledger_confirmation_denied')
    fields(value, ('expected_revision', 'import_id', 'account_id'))
    statement, account = statement_account_check(book, value['import_id'], value['account_id'])
    advance(book, value['expected_revision'])
    require(SourceImport.objects.filter(pk=statement.pk, account__isnull=True).update(account=account) == 1, 'import_account_already_set')
    statement.account = account
    event(book, user, 'statement.account', statement.pk, digest({'import': str(statement.pk), 'account': str(account.pk)}))
    return import_value(statement, False)


def _moves(lines, account_id, amount_units):
    """Whether these lines move one account by exactly this signed amount: in as a debit, out as a credit, never both."""
    moved_in = sum(line.debit_units for line in lines if line.account_id == account_id)
    moved_out = sum(line.credit_units for line in lines if line.account_id == account_id)
    return (moved_in, moved_out) == ((amount_units, 0) if amount_units > 0 else (0, -amount_units))


def _reversed(book):
    """Entries undone by a posted reversal: they no longer settle a statement line."""
    return set(book.journals.filter(source_kind='reversal', state='posted').values_list('source_reference', flat=True))


def _settled(book):
    """Each settled line's entry: the entry it produced, or the entry it was matched to. Reversed entries settle nothing."""
    undone = _reversed(book)
    cited = {journal.source_reference: (journal, 'entry') for journal in book.journals.exclude(state='voided').filter(source_kind='csv')
             if str(journal.pk) not in undone}
    for match in SourceRowMatch.objects.filter(journal__book=book).select_related('journal').order_by('created_at'):
        if str(match.journal_id) not in undone:
            cited[str(match.row_id)] = (match.journal, 'match')
    return cited


def _duplicates(book):
    """Each line marked as a repeat, with the line it repeats."""
    return {str(match.row_id): str(match.duplicate_of_id)
            for match in SourceRowMatch.objects.filter(duplicate_of__isnull=False, row__source_import__book=book)}


def _row(book, row_id):
    try:
        row = SourceRow.objects.filter(pk=identity(row_id), source_import__book=book).select_related('source_import').first()
    except (LedgerError, ValueError):
        row = None
    require(row is not None, 'import_row_not_found')
    return row


def duplicate_check(book, row_id, of_row_id):
    """A pending line and the line of another statement of the same bank account that it repeats. Read-only."""
    row, original = _row(book, row_id), _row(book, of_row_id)
    require(row.pk != original.pk, 'duplicate_mismatch')
    repeats = _duplicates(book)
    require(str(row.pk) not in _settled(book) and str(row.pk) not in repeats, 'match_row_not_pending')
    bank = row.source_import.account_id
    require(bank is not None, 'match_account_required')
    require(original.source_import.account_id == bank and original.date == row.date and original.amount_units == row.amount_units,
            'duplicate_mismatch')
    require(original.source_import_id != row.source_import_id, 'duplicate_same_statement')
    # The original must itself be a real line, and one original answers for one repeat per statement.
    require(str(original.pk) not in repeats and not SourceRowMatch.objects.filter(
        duplicate_of=original, row__source_import=row.source_import).exists(), 'duplicate_mismatch')
    return row, original


@guarded
@transaction.atomic
def mark_duplicate(user, book_id, value):
    """Settle a line as a repeat of another statement's line. No entry is made and no amounts change."""
    user, book = owned(user, book_id, True)
    require(book.created_by_id == user.pk, 'ledger_confirmation_denied')
    fields(value, ('expected_revision', 'row_id', 'of_row_id'))
    row, original = duplicate_check(book, value['row_id'], value['of_row_id'])
    advance(book, value['expected_revision'])
    SourceRowMatch.objects.create(row=row, duplicate_of=original, matched_by=user)
    event(book, user, 'statement.duplicate', row.pk, digest({'row': str(row.pk), 'of': str(original.pk)}))
    return {'row_id': str(row.pk), 'of_row_id': str(original.pk), 'revision': book.revision}


def _taken(book, journal):
    """Bank accounts of this entry already accounted for by a statement line."""
    taken = set()
    if journal.source_kind == 'csv' and str(journal.pk) not in _reversed(book):
        source = SourceRow.objects.filter(pk=journal.source_reference, source_import__book=book).select_related('source_import').first()
        if source is not None and source.source_import.account_id:
            taken.add(source.source_import.account_id)
    for match in journal.matches.select_related('row__source_import'):
        taken.add(match.row.source_import.account_id)
    return taken


def match_check(book, row_id, journal_id):
    """The pending statement line and the entry that already records its movement. Read-only."""
    try:
        row = SourceRow.objects.filter(pk=identity(row_id), source_import__book=book).select_related('source_import').first()
    except (LedgerError, ValueError):
        row = None
    require(row is not None, 'import_row_not_found')
    try:
        journal = book.journals.exclude(state='voided').filter(pk=identity(journal_id)).first()
    except (LedgerError, ValueError):
        journal = None
    require(journal is not None and journal.source_kind != 'reversal', 'ledger_not_found')
    require(str(journal.pk) not in _reversed(book), 'match_entry_reversed')
    require(str(row.pk) not in _settled(book) and str(row.pk) not in _duplicates(book), 'match_row_not_pending')
    bank = row.source_import.account_id
    require(bank is not None, 'match_account_required')
    require(_moves(list(journal.lines.all()), bank, row.amount_units), 'import_account_mismatch')
    require(bank not in _taken(book, journal), 'match_account_taken')
    require(abs((row.date - journal.date).days) <= MATCH_DAYS, 'match_date_too_far')
    require(not closed(book, journal.date) or journal.state == 'posted', 'ledger_period_closed')
    return row, journal


@guarded
@transaction.atomic
def match_row(user, book_id, value):
    """Settle a statement line with an existing entry instead of a second entry. No amounts change."""
    user, book = owned(user, book_id, True)
    require(book.created_by_id == user.pk, 'ledger_confirmation_denied')
    fields(value, ('expected_revision', 'row_id', 'journal_id'))
    row, journal = match_check(book, value['row_id'], value['journal_id'])
    advance(book, value['expected_revision'])
    SourceRowMatch.objects.create(row=row, journal=journal, matched_by=user)
    event(book, user, 'statement.match', row.pk, journal.payload_hash)
    return {'row_id': str(row.pk), 'journal_id': str(journal.pk), 'revision': book.revision}


@guarded
@transaction.atomic
def match_candidates(user, book_id):
    """Pending lines that look like one movement seen twice: opposite lines of two bank accounts, or a line an entry already records."""
    _, book = owned(user, book_id)
    settled, repeats = _settled(book), _duplicates(book)
    banked = [row for row in SourceRow.objects.filter(source_import__book=book, source_import__account__isnull=False)
              .select_related('source_import').order_by('date', 'source_import__created_at', 'row_number')[:MAX_IMPORT_ROWS * 5]
              if str(row.pk) not in repeats]
    pending = [row for row in banked if str(row.pk) not in settled]
    # A line repeats an earlier statement's line of the same account, date, amount and wording; each original answers once per statement.
    duplicates, answered = [], {(str(m.duplicate_of_id), m.row.source_import_id) for m in SourceRowMatch.objects.filter(
        duplicate_of__isnull=False, row__source_import__book=book).select_related('row')}
    for row in pending:
        original = next((other for other in banked if other.source_import_id != row.source_import_id
                         and other.source_import.created_at < row.source_import.created_at
                         and other.source_import.account_id == row.source_import.account_id and other.date == row.date
                         and other.amount_units == row.amount_units and other.description.casefold() == row.description.casefold()
                         and (str(other.pk), row.source_import_id) not in answered), None)
        if original is not None:
            answered.add((str(original.pk), row.source_import_id))
            duplicates.append({'row_id': str(row.pk), 'of_row_id': str(original.pk)})
    repeated = {item['row_id'] for item in duplicates}
    pending = [row for row in pending if str(row.pk) not in repeated]
    transfers, used = [], set()
    for out in (row for row in pending if row.amount_units < 0):
        best = min((row for row in pending if row.pk not in used and row.amount_units == -out.amount_units
                    and row.source_import.account_id != out.source_import.account_id
                    and abs((row.date - out.date).days) <= MATCH_DAYS),
                   key=lambda row: (abs((row.date - out.date).days), row.date, row.row_number), default=None)
        if best is not None:
            used.add(best.pk)
            transfers.append({'out_row_id': str(out.pk), 'in_row_id': str(best.pk), 'amount': money(best.amount_units),
                              'days_apart': abs((best.date - out.date).days)})
    paired = {item[key] for item in transfers for key in ('out_row_id', 'in_row_id')}
    entries = []
    undone = _reversed(book)
    journals = [(journal, list(journal.lines.all()), _taken(book, journal))
                for journal in book.journals.exclude(source_kind='reversal').exclude(state='voided').order_by('sequence')[:2000] if str(journal.pk) not in undone]
    for row in pending:
        if str(row.pk) in paired:
            continue
        bank = row.source_import.account_id
        for journal, lines, taken in journals:
            if bank not in taken and abs((row.date - journal.date).days) <= MATCH_DAYS and _moves(lines, bank, row.amount_units):
                entries.append({'row_id': str(row.pk), 'journal_id': str(journal.pk), 'days_apart': abs((row.date - journal.date).days)})
                break
    return {'book_id': str(book.pk), 'revision': book.revision, 'transfers': transfers[:200], 'entries': entries[:200],
            'duplicates': duplicates[:200]}


@guarded
@transaction.atomic
def source_rows(user, book_id):
    """Imported statement rows, oldest first, each pending or settled by its own entry or by a match to another entry."""
    _, book = owned(user, book_id)
    settled, repeats = _settled(book), _duplicates(book)
    rows = []
    for row in SourceRow.objects.filter(source_import__book=book).select_related('source_import').order_by(
            'date', 'source_import__created_at', 'row_number')[:MAX_IMPORT_ROWS * 5]:
        journal, via = settled.get(str(row.pk), (None, None))
        rows.append({'id': str(row.pk), 'import_id': str(row.source_import_id), 'import_filename': row.source_import.filename,
                     'row_number': row.row_number, 'date': row.date.isoformat(), 'description': row.description,
                     'amount': money(row.amount_units), 'reference': row.reference,
                     'account_id': str(row.source_import.account_id) if row.source_import.account_id else None,
                     'status': ('duplicate' if str(row.pk) in repeats else 'pending' if journal is None
                                else 'posted' if journal.state == 'posted' else 'proposed'),
                     'journal_id': None if journal is None else str(journal.pk),
                     'via': 'duplicate' if str(row.pk) in repeats else via, 'duplicate_of': repeats.get(str(row.pk))})
    return {'book_id': str(book.pk), 'currency': book.currency, 'revision': book.revision, 'rows': rows}

def _statements(book, accounts, start, end):
    """Statements for one range, with their totals in exact units."""
    require(book.period_start <= start <= end <= book.period_end, 'ledger_date_outside_period')
    in_range = dict.fromkeys(accounts, 0)   # debit minus credit inside the range
    to_date = dict.fromkeys(accounts, 0)    # debit minus credit up to the end date
    entries = {}                            # lines of each entry inside the range, for the cash flow
    lines = JournalLine.objects.filter(journal__book=book, journal__state='posted', journal__date__lte=end).select_related('journal')
    for line in lines.iterator(chunk_size=1000):
        net = line.debit_units - line.credit_units
        to_date[line.account_id] += net
        if line.journal.date >= start:
            in_range[line.account_id] += net
            entries.setdefault(line.journal_id, []).append((line.account_id, net))

    def section(category, source, sign):
        rows = [(a, sign * source[pk]) for pk, a in accounts.items() if a.category == category and source[pk] != 0]
        rows.sort(key=lambda item: item[0].code)
        return [{**account_value(a), 'amount': money(units)} for a, units in rows], sum(units for _, units in rows)

    income, total_income = section('income', in_range, -1)
    expenses, total_expenses = section('expense', in_range, 1)
    assets, total_assets = section('asset', to_date, 1)
    liabilities, total_liabilities = section('liability', to_date, -1)
    equity, total_equity = section('equity', to_date, -1)
    # Income and expense accounts are not closed into equity, so earnings to date complete the equation.
    earnings = -sum(units for pk, units in to_date.items() if accounts[pk].category in ('income', 'expense'))
    # Cash flow: an entry that touches a bank or cash account moves cash by exactly the opposite of its
    # other lines, because the entry balances. Transfers between cash accounts and entries without cash move none.
    cash = sorted((a for a in accounts.values() if a.cash), key=lambda a: a.code)
    cash_ids = {a.pk for a in cash}
    moved = dict.fromkeys(accounts, 0)
    for parts in entries.values():
        if any(pk in cash_ids for pk, _ in parts):
            for pk, net in parts:
                if pk not in cash_ids:
                    moved[pk] -= net
    movements = sorted(((accounts[pk], units) for pk, units in moved.items() if units != 0), key=lambda item: item[0].code)
    closing = sum(to_date[a.pk] for a in cash)
    change = sum(in_range[a.pk] for a in cash)
    require(change == sum(units for _, units in movements), 'ledger_invariant_failed')
    cash_flow = {'accounts': [{**account_value(a), 'opening': money(to_date[a.pk] - in_range[a.pk]), 'closing': money(to_date[a.pk])} for a in cash],
                 'opening_cash': money(closing - change), 'closing_cash': money(closing), 'net_change': money(change),
                 'received': money(sum(units for _, units in movements if units > 0)),
                 'paid': money(sum(units for _, units in movements if units < 0)),
                 'movements': [{**account_value(a), 'amount': money(units)} for a, units in movements]}
    # Bridge from profit to cash: income and expenses that have not moved cash yet, then cash moved by other accounts.
    net_income = total_income - total_expenses
    earned_in_cash = sum(units for a, units in movements if a.category in ('income', 'expense'))
    cash_flow['bridge'] = {'net_income': money(net_income), 'not_in_cash': money(earned_in_cash - net_income),
                           'other_accounts': money(change - earned_in_cash), 'net_change': money(change)}
    totals = {'total_income': total_income, 'total_expenses': total_expenses, 'net_income': net_income,
              'total_assets': total_assets, 'total_liabilities': total_liabilities, 'total_equity': total_equity,
              'earnings_to_date': earnings, 'net_cash_change': change}
    return {'from': start.isoformat(), 'to': end.isoformat(), 'cash_flow': cash_flow,
            'income_statement': {'income': income, 'expenses': expenses, 'total_income': money(total_income),
                                 'total_expenses': money(total_expenses), 'net_income': money(total_income - total_expenses)},
            'balance_sheet': {'as_of': end.isoformat(), 'assets': assets, 'liabilities': liabilities, 'equity': equity,
                              'total_assets': money(total_assets), 'total_liabilities': money(total_liabilities),
                              'total_equity': money(total_equity), 'earnings_to_date': money(earnings),
                              'total_liabilities_and_equity': money(total_liabilities + total_equity + earnings)}}, totals


@guarded
@transaction.atomic
def financial_report(user, book_id, value):
    """Income statement and cash flow for a date range and balance sheet as of its end, from posted entries only.

    With compare_from and compare_to, also the same statements for that second range and the exact
    change in each total, including the change in cash: the requested range minus the compared range.
    """
    _, book = owned(user, book_id)
    require(type(value) is dict)
    compared = 'compare_from' in value or 'compare_to' in value
    fields(value, ('from', 'to', 'compare_from', 'compare_to') if compared else ('from', 'to'))
    accounts = {a.pk: a for a in book.accounts.all()}
    current, totals = _statements(book, accounts, day(value['from']), day(value['to']))
    result = {'book_id': str(book.pk), 'currency': book.currency, 'revision': book.revision, **current,
              'pending_count': book.journals.filter(state='draft').count()}
    if compared:
        other, other_totals = _statements(book, accounts, day(value['compare_from']), day(value['compare_to']))
        result['comparison'] = {**other, 'change': {key: money(totals[key] - other_totals[key]) for key in totals}}
    return result


RELEASE_TOTALS = ('total_income', 'total_expenses', 'net_income', 'total_assets', 'total_liabilities', 'total_equity', 'earnings_to_date', 'closing_cash')
MAX_RELEASES = 500


def _release_figures(book, start, end):
    """The statements of a range with the totals and a hash of every figure (account codes and amounts, not names)."""
    statements, units = _statements(book, {a.pk: a for a in book.accounts.all()}, start, end)
    totals = {**{key: money(units[key]) for key in RELEASE_TOTALS if key in units}, 'closing_cash': statements['cash_flow']['closing_cash']}
    rows = lambda items: [[item['code'], item.get('amount', item.get('closing'))] for item in items]
    figures = digest({'from': statements['from'], 'to': statements['to'], 'currency': book.currency, 'totals': totals,
        'income': rows(statements['income_statement']['income']), 'expenses': rows(statements['income_statement']['expenses']),
        'assets': rows(statements['balance_sheet']['assets']), 'liabilities': rows(statements['balance_sheet']['liabilities']),
        'equity': rows(statements['balance_sheet']['equity']), 'cash': rows(statements['cash_flow']['accounts']),
        'movements': rows(statements['cash_flow']['movements']), 'opening_cash': statements['cash_flow']['opening_cash']})
    return {**statements, 'currency': book.currency}, totals, figures


def release_check(user, book, value):
    """Statements can be released only for a range that ends in a closed month, from books with no integrity findings. Read-only."""
    fields(value, ('from', 'to', 'title'))
    start, end, title = day(value['from']), day(value['to']), text(value['title'], 120)
    statements, totals, figures = _release_figures(book, start, end)
    require(closed(book, end), 'report_period_not_closed')
    require(not verify_book(user, str(book.pk))['findings'], 'report_integrity_findings')
    previous = book.releases.filter(period_from=start, period_to=end).order_by('-number').first()
    require(previous is None or previous.figures_hash != figures, 'report_already_released')
    require(book.releases.count() < MAX_RELEASES, 'report_release_limit')
    return {'from': start, 'to': end, 'title': title, 'statements': statements, 'totals': totals, 'figures': figures,
            'number': (book.releases.order_by('-number').values_list('number', flat=True).first() or 0) + 1}


@guarded
@transaction.atomic
def release_report(user, book_id, value):
    """Record the approved release: the statements as they stand, kept exactly. No entry or amount changes."""
    user, book = owned(user, book_id, True)
    require(book.created_by_id == user.pk, 'ledger_confirmation_denied')
    require(type(value) is dict)
    checked = release_check(user, book, {key: item for key, item in value.items() if key != 'expected_revision'})
    advance(book, value.get('expected_revision'))
    row = ReportRelease.objects.create(book=book, number=checked['number'], title=checked['title'], period_from=checked['from'], period_to=checked['to'],
        statements=checked['statements'], totals=checked['totals'], figures_hash=checked['figures'], book_revision=book.revision, released_by=user)
    event(book, user, 'report.release', row.pk, checked['figures'])
    return {'release_id': str(row.pk), 'number': row.number, 'figures_hash': row.figures_hash, 'revision': book.revision}


@guarded
@transaction.atomic
def report_releases(user, book_id, value):
    """Released statements, newest first, each marked unchanged (the books still give these figures) and latest (no newer
    release of the same range). With release_id: that release's kept statements and every total that differs today. Read-only."""
    user, book = owned(user, book_id)
    require(type(value) is dict)
    fields(value, ('release_id',) if 'release_id' in value else ())
    rows = list(book.releases.order_by('-number')[:MAX_RELEASES])
    today, newest = {}, {}
    for row in rows:
        key = (row.period_from, row.period_to)
        newest.setdefault(key, row.number)
        if key not in today:
            try:
                today[key] = _release_figures(book, *key)
            except LedgerError:
                today[key] = (None, {}, None)       # The book period no longer contains this range.

    def shown(row):
        key = (row.period_from, row.period_to)
        return {'id': str(row.pk), 'number': row.number, 'title': row.title, 'from': row.period_from.isoformat(), 'to': row.period_to.isoformat(),
                'released_at': row.created_at.isoformat(), 'figures_hash': row.figures_hash, 'totals': row.totals,
                'unchanged': today[key][2] == row.figures_hash, 'latest': newest[key] == row.number}
    result = {'book_id': str(book.pk), 'currency': book.currency, 'revision': book.revision,
              'closed_through': book.closed_through.isoformat() if book.closed_through else None, 'releases': [shown(row) for row in rows]}
    if 'release_id' in value:
        try:
            wanted = uuid.UUID(value['release_id']) if type(value['release_id']) is str else None
        except ValueError:
            wanted = None
        row = next((item for item in rows if item.pk == wanted), None)
        require(row is not None, 'report_release_not_found')
        now = today[(row.period_from, row.period_to)][1]
        result['release'] = {**shown(row), 'statements': row.statements,
                             'changes': [{'total': key, 'released': row.totals[key], 'now': now.get(key)} for key in RELEASE_TOTALS
                                         if key in row.totals and now.get(key) != row.totals[key]]}
    return result


def closed(book, dated):
    return book.closed_through is not None and dated <= book.closed_through


def check_period_change(book, action, through):
    """Validate closing through a later date, or reopening to an earlier date or fully (None)."""
    if action == 'close':
        target = day(through)
        require(book.period_start <= target <= book.period_end, 'ledger_date_outside_period')
        require(book.closed_through is None or target > book.closed_through, 'ledger_period_already_closed')
        require(not book.journals.filter(state='draft', date__lte=target).exists(), 'ledger_period_has_drafts')
        return target
    require(action == 'reopen' and book.closed_through is not None, 'ledger_period_not_closed')
    if through is None:
        return None
    target = day(through)
    require(book.period_start <= target < book.closed_through, 'ledger_period_reopen_invalid')
    return target


def _month_ends(start, end):
    """The last day of each calendar month touched by the period, with the period end for its final month."""
    months, year, month = [], start.year, start.month
    while (year, month) <= (end.year, end.month) and len(months) < 120:
        following = date(year + (month == 12), month % 12 + 1, 1)
        months.append((f'{year:04d}-{month:02d}', min(following - timedelta(days=1), end)))
        year, month = following.year, following.month
    return months


@guarded
@transaction.atomic
def close_review(user, book_id, value):
    """What stands between the books and closing them through a date. Read-only; nothing is changed or decided.

    Four checks, each with its outstanding items: draft entries dated on or before the date; statement lines
    up to the date with no posted entry; bank statements ending in the period being closed that are not
    reconciled as they stand today; and the integrity of every stored entry. `can_close` is the hard rule
    the close itself enforces; `ready` also requires every check to be clear.
    """
    user, book = owned(user, book_id)
    fields(value, ('through',))
    through = day(value['through'])
    require(book.period_start <= through <= book.period_end, 'ledger_date_outside_period')
    blocking = None
    try:
        check_period_change(book, 'close', value['through'])
    except LedgerError as refused:
        blocking = refused.code
    drafts = [{'journal_id': str(j.pk), 'sequence': j.sequence, 'date': j.date.isoformat(), 'description': j.description, 'amount': money(j.total_units)}
              for j in book.journals.filter(state='draft', date__lte=through).order_by('date', 'sequence')]
    lines = [{'row_id': r['id'], 'date': r['date'], 'description': r['description'], 'amount': r['amount'], 'status': r['status']}
             for r in source_rows(user, book_id)['rows'] if r['status'] in ('pending', 'proposed') and r['date'] <= through.isoformat()]
    open_statements = []
    for statement in book.imports.select_related('account').order_by('created_at')[:500]:
        dates = sorted(statement.rows.values_list('date', flat=True))
        if not dates or dates[-1] > through or (book.closed_through is not None and dates[-1] <= book.closed_through):
            continue
        if statement.account is None:
            state = 'needs_account'
        elif statement.closing_units is None or statement.opening_units is None:
            state = 'needs_balances'
        else:
            found = _reconcile(book, statement)
            recorded = _recorded(book, statement, found['figures'])
            if recorded is not None:
                state = None if recorded['unchanged'] else 'changed'
            else:
                state = 'not_recorded' if found['adjusted_books'] == found['adjusted_bank'] else 'difference'
        if state is not None:
            open_statements.append({'import_id': str(statement.pk), 'filename': statement.filename, 'last_date': dates[-1].isoformat(),
                                    'account': f'{statement.account.code} {statement.account.name}' if statement.account else None, 'state': state})
    findings = verify_book(user, book_id)['findings']
    checks = [{'key': key, 'ok': not items, 'count': len(items), 'items': items[:50]}
              for key, items in (('drafts', drafts), ('statement_lines', lines), ('reconciliations', open_statements), ('integrity', findings))]
    return {'book_id': str(book.pk), 'currency': book.currency, 'revision': book.revision, 'through': through.isoformat(),
            'period_start': book.period_start.isoformat(), 'period_end': book.period_end.isoformat(),
            'closed_through': book.closed_through.isoformat() if book.closed_through else None,
            'calendar': [{'month': month, 'ends': ends.isoformat(), 'state': 'closed' if book.closed_through and ends <= book.closed_through else 'open'}
                         for month, ends in _month_ends(book.period_start, book.period_end)],
            'checks': checks, 'can_close': blocking is None, 'blocking': blocking,
            'ready': blocking is None and all(item['ok'] for item in checks)}


def accrual_check(book, value):
    """An adjusting entry and the later date its exact opposite posts on, both in open months of the period. Read-only."""
    fields(value, ('date', 'description', 'lines', 'reverse_on'))
    dated, reverse_on = day(value['date']), day(value['reverse_on'])
    require(book.period_start <= dated <= book.period_end and book.period_start <= reverse_on <= book.period_end, 'ledger_date_outside_period')
    require(reverse_on > dated, 'accrual_reverse_not_after')
    require(not closed(book, dated) and not closed(book, reverse_on), 'ledger_period_closed')
    checked = schedule_check(book, {'description': value['description'], 'lines': value['lines'], 'day': 1, 'starts': value['date']})
    return {'date': dated, 'reverse_on': reverse_on, 'description': checked['description'], 'lines': checked['lines'], 'total': checked['total']}


@guarded
@transaction.atomic
def post_accrual(user, book_id, value):
    """Post an approved accrual and its reversal together. If either cannot post, neither does."""
    user, book = owned(user, book_id, True)
    require(book.created_by_id == user.pk, 'ledger_confirmation_denied')
    expected = value.get('expected_revision') if type(value) is dict else None
    checked = accrual_check(book, {key: item for key, item in value.items() if key != 'expected_revision'})
    require(book.revision == expected, 'ledger_revision_conflict')
    reference = f'accrual-{uuid.uuid4()}'
    made = propose_journal(user, book_id, {
        'operation_id': str(uuid.uuid4()), 'expected_revision': expected, 'date': checked['date'].isoformat(), 'description': checked['description'],
        'source': {'kind': 'accrual', 'reference': reference}, 'evidence': [f'Accrual approved together with its reversal on {checked["reverse_on"].isoformat()}'],
        'lines': checked['lines']}, _kind='accrual')
    confirm_own_journal(user, book_id, made['id'], {'expected_revision': expected + 1, 'payload_hash': made['payload_hash'], 'confirm': True})
    reversed_entry = reverse_journal(user, book_id, made['id'], {'expected_revision': expected + 2, 'date': checked['reverse_on'].isoformat()})
    return {'journal_id': made['id'], 'reversal_id': reversed_entry['journal_id'], 'date': checked['date'].isoformat(), 'reverse_on': checked['reverse_on'].isoformat()}


MAX_SCHEDULES = 50


def schedule_check(book, value):
    """A balanced template of this book's accounts, a day of the month and a range inside the period. Read-only."""
    fields(value, ('description', 'lines', 'day', 'starts', *(('ends',) if 'ends' in value else ())))
    description = text(value['description'], 500)
    require(type(value['day']) is int and 1 <= value['day'] <= 31)
    starts = day(value['starts'])
    ends = day(value['ends']) if 'ends' in value else None
    require(book.period_start <= starts <= book.period_end and (ends is None or ends <= book.period_end), 'ledger_date_outside_period')
    require(ends is None or ends >= starts, 'schedule_ends_before_start')
    require(type(value['lines']) is list and 2 <= len(value['lines']) <= 100)
    lines, debit, credit = [], 0, 0
    for line in value['lines']:
        fields(line, ('account_id', 'debit', 'credit'))
        account = book.accounts.filter(pk=identity(line['account_id'])).first()
        require(account is not None, 'ledger_account_not_found')
        left, right = units(line['debit']), units(line['credit'])
        require((left > 0 and right == 0) or (right > 0 and left == 0))
        debit += left; credit += right
        lines.append({'account_id': str(account.pk), 'debit': money(left), 'credit': money(right)})
    require(0 < debit == credit <= MAX_UNITS, 'ledger_unbalanced')
    require(book.schedules.filter(stopped_at__isnull=True).count() < MAX_SCHEDULES, 'schedule_limit')
    return {'description': description, 'lines': lines, 'day': value['day'], 'starts': starts, 'ends': ends, 'total': debit}


def schedule_value(row):
    total = sum(units(line['debit']) for line in row.lines)
    return {'id': str(row.pk), 'description': row.description, 'day': row.day, 'starts': row.starts.isoformat(),
            'ends': row.ends.isoformat() if row.ends else None, 'active': row.stopped_at is None, 'total': money(total), 'lines': row.lines}


@guarded
@transaction.atomic
def create_schedule(user, book_id, value):
    """Save an approved recurring entry. Nothing is drafted or posted here."""
    user, book = owned(user, book_id, True)
    require(book.created_by_id == user.pk, 'ledger_confirmation_denied')
    expected = value.get('expected_revision') if type(value) is dict else None
    checked = schedule_check(book, {key: item for key, item in value.items() if key != 'expected_revision'})
    advance(book, expected)
    row = RecurringEntry.objects.create(book=book, description=checked['description'], lines=checked['lines'], day=checked['day'],
                                        starts=checked['starts'], ends=checked['ends'], created_by=user)
    event(book, user, 'schedule.create', row.pk, digest(schedule_value(row)))
    return schedule_value(row)


def stop_check(book, schedule_id):
    try:
        row = book.schedules.filter(pk=identity(schedule_id)).first()
    except (LedgerError, ValueError):
        row = None
    require(row is not None, 'schedule_not_found')
    require(row.stopped_at is None, 'schedule_not_active')
    return row


@guarded
@transaction.atomic
def stop_schedule(user, book_id, value):
    """Stop a recurring entry. Drafts and posted entries it made are not touched."""
    user, book = owned(user, book_id, True)
    require(book.created_by_id == user.pk, 'ledger_confirmation_denied')
    fields(value, ('expected_revision', 'schedule_id'))
    row = stop_check(book, value['schedule_id'])
    advance(book, value['expected_revision'])
    require(RecurringEntry.objects.filter(pk=row.pk, stopped_at__isnull=True).update(stopped_at=timezone.now()) == 1, 'schedule_not_active')
    row.refresh_from_db()
    event(book, user, 'schedule.stop', row.pk, digest(schedule_value(row)))
    return schedule_value(row)


def _occurrences(row, through):
    """The template's dates on or before `through`: its day in each month, moved back to the month's last day when shorter."""
    found, year, month = [], row.starts.year, row.starts.month
    last = min(through, row.ends) if row.ends else through
    while len(found) < 400:
        following = date(year + (month == 12), month % 12 + 1, 1)
        when = min(date(year, month, 1) + timedelta(days=row.day - 1), following - timedelta(days=1))
        if when > last:
            break
        if when >= row.starts:
            found.append(when)
        year, month = following.year, following.month
    return found


def _made(book):
    """Occurrences that already have a draft or posted entry (discarded drafts do not count)."""
    return set(book.journals.filter(source_kind='schedule').exclude(state='voided').values_list('source_reference', flat=True))


def _due(book, row, through, made):
    if row.stopped_at is not None:
        return []
    return [when for when in _occurrences(row, through)
            if not closed(book, when) and book.period_start <= when <= book.period_end and f'{row.pk}:{when.isoformat()}' not in made]


@guarded
@transaction.atomic
def schedules(user, book_id, value):
    """Every recurring entry of the book with the occurrences due on or before a date that have no entry yet. Read-only."""
    _, book = owned(user, book_id)
    fields(value, ('through',))
    through = day(value['through'])
    require(book.period_start <= through <= book.period_end, 'ledger_date_outside_period')
    made, listed = _made(book), []
    for row in book.schedules.order_by('created_at')[:MAX_SCHEDULES * 4]:
        due = _due(book, row, through, made)
        later = [when for when in _occurrences(row, book.period_end) if when > through] if row.stopped_at is None else []
        listed.append({**schedule_value(row), 'due': [when.isoformat() for when in due[:24]],
                       'next': later[0].isoformat() if later else None})
    return {'book_id': str(book.pk), 'currency': book.currency, 'revision': book.revision, 'through': through.isoformat(), 'schedules': listed}


@guarded
@transaction.atomic
def prepare_due(user, book_id, value):
    """Draft every due occurrence on or before a date, once each. Drafts are proposals: posting each one needs approval."""
    user, book = owned(user, book_id, True)
    fields(value, ('through',))
    through = day(value['through'])
    require(book.period_start <= through <= book.period_end, 'ledger_date_outside_period')
    made, drafts = _made(book), []
    for row in book.schedules.filter(stopped_at__isnull=True).order_by('created_at'):
        for when in _due(book, row, through, made)[:24]:
            reference = f'{row.pk}:{when.isoformat()}'
            # One operation per attempt: a discarded draft keeps its id, so the next draft of the same date needs a new one.
            attempt = book.journals.filter(source_kind='schedule', source_reference=reference).count()
            book.refresh_from_db()
            drafts.append(propose_journal(user, book_id, {
                'operation_id': str(uuid.uuid5(row.pk, f'{reference}:{attempt}')), 'expected_revision': book.revision, 'date': when.isoformat(),
                'description': row.description, 'source': {'kind': 'schedule', 'reference': reference},
                'evidence': [f'Recurring entry approved on {row.created_at.date().isoformat()}, due {when.isoformat()}'], 'lines': row.lines}, _kind='schedule'))
    drafts.sort(key=lambda item: (item['date'], item['sequence']))
    return {'book_id': str(book.pk), 'through': through.isoformat(), 'drafts': drafts}


@guarded
@transaction.atomic
def change_period(user, book_id, value):
    """Close the books through a date, or reopen them. Posted history is never altered."""
    user, book = owned(user, book_id, True)
    require(book.created_by_id == user.pk, 'ledger_confirmation_denied')
    fields(value, ('expected_revision', 'action', 'through'))
    target = check_period_change(book, value['action'], value['through'])
    advance(book, value['expected_revision'])
    Book.objects.filter(pk=book.pk).update(closed_through=target)
    event(book, user, 'period.' + value['action'], book.pk, digest({'through': target.isoformat() if target else None}))
    return {'book_id': str(book.pk), 'closed_through': target.isoformat() if target else None, 'revision': book.revision}


def reversal_lines(journal):
    """The exact opposite of a journal's lines, in the same order."""
    return [{'account_id': str(line.account_id), 'debit': money(line.credit_units), 'credit': money(line.debit_units)}
            for line in journal.lines.order_by('position')]


@guarded
@transaction.atomic
def reverse_journal(user, book_id, journal_id, value):
    """Undo a posted entry by posting its linked opposite. The original is never edited; each entry reverses once."""
    user, book = owned(user, book_id, True)
    require(book.created_by_id == user.pk, 'ledger_confirmation_denied')
    fields(value, ('expected_revision', 'date'))
    original = book.journals.filter(pk=identity(journal_id), state='posted').first()
    require(original is not None, 'ledger_not_found')
    require(original.source_kind != 'reversal', 'ledger_reversal_not_reversible')
    require(not book.journals.filter(source_kind='reversal', source_reference=str(original.pk)).exists(), 'ledger_already_reversed')
    entry = propose_journal(user, book_id, {
        'operation_id': str(uuid.uuid4()), 'expected_revision': value['expected_revision'], 'date': value['date'],
        'description': ('Reversal of: ' + original.description)[:500],
        'source': {'kind': 'reversal', 'reference': str(original.pk)},
        'evidence': [f'Reverses entry {original.sequence} dated {original.date.isoformat()}'],
        'lines': reversal_lines(original)}, _kind='reversal')
    return confirm_own_journal(user, book_id, entry['id'], {'expected_revision': value['expected_revision'] + 1,
                                                            'payload_hash': entry['payload_hash'], 'confirm': True})


def discard_check(book, journal_id):
    """A draft entry of this book that can be discarded. Read-only."""
    try:
        row = book.journals.filter(pk=identity(journal_id)).first()
    except (LedgerError, ValueError):
        row = None
    require(row is not None, 'ledger_not_found')
    require(row.state == 'draft', 'ledger_not_draft')
    return row


@guarded
@transaction.atomic
def discard_journal(user, book_id, journal_id, value):
    """Discard a draft entry. It never posted, so no amounts change; the record stays, and its statement line or reference is free again."""
    user, book = owned(user, book_id, True)
    require(book.created_by_id == user.pk, 'ledger_confirmation_denied')
    fields(value, ('expected_revision',))
    row = discard_check(book, journal_id)
    advance(book, value['expected_revision'])
    require(Journal.objects.filter(pk=row.pk, state='draft').update(state='voided') == 1, 'ledger_not_draft')
    event(book, user, 'journal.discard', row.pk, row.payload_hash)
    return {'journal_id': str(row.pk), 'state': 'discarded'}


@guarded
@transaction.atomic
def verify_book(user, book_id):
    """Recheck every stored entry against the double-entry rules, independently of how it was written.

    Read-only. Each finding names the rule and the entry; a clean book has none.
    """
    _, book = owned(user, book_id)
    accounts = set(book.accounts.values_list('pk', flat=True))
    journals = list(book.journals.order_by('sequence'))
    lines = {journal.pk: [] for journal in journals}
    for line in JournalLine.objects.filter(journal__book=book).order_by('position'):
        lines[line.journal_id].append(line)
    receipts = {row.journal_id: row for row in PostingReceipt.objects.filter(journal__book=book)}
    by_id = {journal.pk: journal for journal in journals}
    findings, posted_debit, posted_credit = [], 0, 0

    def found(code, journal=None):
        findings.append({'code': code, 'journal_id': str(journal.pk) if journal else None,
                         'sequence': journal.sequence if journal else None})

    for journal in journals:
        own = lines[journal.pk]
        debit, credit = sum(line.debit_units for line in own), sum(line.credit_units for line in own)
        if journal.state == 'posted':
            posted_debit += debit; posted_credit += credit
        if not (2 <= len(own) <= 100 and [line.position for line in own] == list(range(len(own)))
                and all((line.debit_units > 0 and line.credit_units == 0) or (line.credit_units > 0 and line.debit_units == 0) for line in own)):
            found('journal_lines_invalid', journal)
        if not 0 < debit == credit == journal.total_units:
            found('journal_unbalanced', journal)
        if any(line.account_id not in accounts for line in own):
            found('line_account_foreign', journal)
        payload = {'book_id': str(book.pk), 'entity_id': str(book.entity_id), 'currency': book.currency,
                   'date': journal.date.isoformat(), 'description': journal.description,
                   'source': {'kind': journal.source_kind, 'reference': journal.source_reference}, 'evidence': journal.evidence,
                   'lines': [{'account_id': str(line.account_id), 'debit': money(line.debit_units),
                              'credit': money(line.credit_units)} for line in own]}
        if digest(payload) != journal.payload_hash:
            found('journal_hash_mismatch', journal)
        receipt = receipts.get(journal.pk)
        if (journal.state == 'posted') != (receipt is not None) or (receipt is not None and receipt.payload_hash != journal.payload_hash):
            found('posting_receipt_invalid', journal)
        if journal.source_kind == 'reversal':
            try:
                original = by_id.get(uuid.UUID(journal.source_reference))
            except ValueError:
                original = None
            mirrored = original is not None and original.state == 'posted' and original.source_kind != 'reversal' and [
                (line.account_id, line.credit_units, line.debit_units) for line in lines[original.pk]] == [
                (line.account_id, line.debit_units, line.credit_units) for line in own]
            if not mirrored:
                found('reversal_invalid', journal)
    for match in SourceRowMatch.objects.filter(journal__book=book).select_related('journal', 'row__source_import'):
        bank = match.row.source_import.account_id
        if bank is None or match.row.source_import.book_id != book.pk or not _moves(lines[match.journal_id], bank, match.row.amount_units):
            found('row_match_invalid', match.journal)
    for match in SourceRowMatch.objects.filter(duplicate_of__isnull=False, row__source_import__book=book).select_related(
            'row__source_import', 'duplicate_of__source_import'):
        row, original = match.row, match.duplicate_of
        if (row.source_import.account_id is None or original.source_import.account_id != row.source_import.account_id
                or original.source_import.book_id != book.pk or original.source_import_id == row.source_import_id
                or original.date != row.date or original.amount_units != row.amount_units):
            found('row_match_invalid')
    revisions = sorted(LedgerEvent.objects.filter(book=book).values_list('revision', flat=True))
    if revisions != list(range(1, book.revision + 1)):
        found('event_history_incomplete')
    if posted_debit != posted_credit:
        found('posted_totals_unequal')
    return {'book_id': str(book.pk), 'revision': book.revision, 'ok': not findings, 'journals_checked': len(journals),
            'posted_count': sum(1 for journal in journals if journal.state == 'posted'),
            'draft_count': sum(1 for journal in journals if journal.state == 'draft'),
            'posted_debit': money(posted_debit), 'posted_credit': money(posted_credit), 'findings': findings}


OPENING_EQUITY = '3900'


def opening_entry(book, value):
    """The exact opening entry for supplied balances. Read-only.

    The supplied figures must add up to the control totals from the source records. Any
    difference between their debits and credits goes to opening balance equity, shown as its own line.
    """
    fields(value, ('balances', 'control'))
    fields(value['control'], ('debit', 'credit'))
    require(type(value['balances']) is list and 1 <= len(value['balances']) <= 98)
    require(not closed(book, book.period_start), 'ledger_period_closed')
    for earlier in book.journals.filter(source_kind='opening'):
        require(earlier.state == 'posted' and book.journals.filter(source_kind='reversal', source_reference=str(earlier.pk),
                                                                   state='posted').exists(), 'ledger_opening_exists')
    accounts = {account.pk: account for account in book.accounts.all()}
    equity = next((account for account in accounts.values() if account.code == OPENING_EQUITY and account.category == 'equity'), None)
    require(equity is not None, 'ledger_opening_equity_missing')
    lines, seen, debit, credit = [], set(), 0, 0
    for item in value['balances']:
        fields(item, ('account_id', 'debit', 'credit'))
        account = accounts.get(identity(item['account_id']))
        require(account is not None, 'ledger_account_not_found')
        require(account.pk != equity.pk and account.pk not in seen, 'ledger_opening_account_invalid')
        seen.add(account.pk)
        left, right = units(item['debit']), units(item['credit'])
        require((left > 0 and right == 0) or (right > 0 and left == 0))
        debit += left; credit += right
        lines.append({'account_id': str(account.pk), 'account': f'{account.code} {account.name}',
                      'debit': money(left), 'credit': money(right), 'offset': False})
    require(units(value['control']['debit']) == debit and units(value['control']['credit']) == credit, 'ledger_opening_control_mismatch')
    if debit != credit:
        lines.append({'account_id': str(equity.pk), 'account': f'{equity.code} {equity.name}',
                      'debit': money(max(credit - debit, 0)), 'credit': money(max(debit - credit, 0)), 'offset': True})
    total = max(debit, credit)
    require(len(lines) >= 2 and 0 < total <= MAX_UNITS, 'ledger_unbalanced')
    return {'date': book.period_start.isoformat(), 'number': book.journals.filter(source_kind='opening').count() + 1,
            'lines': lines, 'total_debit': money(total), 'total_credit': money(total),
            'control_debit': money(debit), 'control_credit': money(credit)}


@guarded
@transaction.atomic
def post_opening(user, book_id, value):
    """Post the opening entry for the supplied balances. One active opening entry per book; reverse it to correct it."""
    user, book = owned(user, book_id, True)
    require(book.created_by_id == user.pk, 'ledger_confirmation_denied')
    fields(value, ('expected_revision', 'balances', 'control'))
    built = opening_entry(book, {'balances': value['balances'], 'control': value['control']})
    entry = propose_journal(user, book_id, {
        'operation_id': str(uuid.uuid4()), 'expected_revision': value['expected_revision'], 'date': built['date'],
        'description': 'Opening balances', 'source': {'kind': 'opening', 'reference': f"opening-{built['number']}"},
        'evidence': [f"Supplied totals: debits {built['control_debit']}, credits {built['control_credit']}"],
        'lines': [{'account_id': line['account_id'], 'debit': line['debit'], 'credit': line['credit']} for line in built['lines']]},
        _kind='opening')
    return confirm_own_journal(user, book_id, entry['id'], {'expected_revision': value['expected_revision'] + 1,
                                                            'payload_hash': entry['payload_hash'], 'confirm': True})


NORMAL_DEBIT = ('asset', 'expense')


@guarded
@transaction.atomic
def account_activity(user, book_id, account_id):
    """Posted lines in date/sequence order with exact running balances; drafts listed apart."""
    _, book = owned(user, book_id)
    account = book.accounts.filter(pk=identity(account_id)).first()
    require(account is not None, 'ledger_not_found')
    sign = 1 if account.category in NORMAL_DEBIT else -1
    posted, pending = [], []
    balance = debit_total = credit_total = 0
    lines = (JournalLine.objects.filter(journal__book=book, account=account).exclude(journal__state='voided').select_related('journal')
             .order_by('journal__date', 'journal__sequence', 'position'))
    for line in lines.iterator(chunk_size=1000):
        journal = line.journal
        row = {'journal_id': str(journal.pk), 'sequence': journal.sequence, 'date': journal.date.isoformat(),
               'description': journal.description,
               'source': {'kind': journal.source_kind, 'reference': journal.source_reference},
               'debit': money(line.debit_units), 'credit': money(line.credit_units)}
        if journal.state != 'posted':
            pending.append(row)
            continue
        debit_total += line.debit_units; credit_total += line.credit_units
        balance += sign * (line.debit_units - line.credit_units)
        posted.append({**row, 'balance': money(balance)})
    return {'book_id': str(book.pk), 'currency': book.currency, 'revision': book.revision,
            'account': account_value(account), 'normal_side': 'debit' if sign == 1 else 'credit',
            'posted': posted, 'pending': pending, 'posted_debit': money(debit_total), 'posted_credit': money(credit_total),
            'balance': money(balance)}

@guarded
@transaction.atomic
def list_books(user):
    user = current(user)
    entities = intake.Entity.objects.filter(engagement__in=intake.owned_engagements(user)).select_related('engagement')
    # Entity quotas are inherited from intake. No provider or credential records.
    return {'actor_id': str(user.pk),
            'entities': [{'id': str(e.pk), 'name': e.name, 'client': e.engagement.name} for e in entities.order_by('name', 'id')],
            'books': [{'id': str(b.pk), 'entity_id': str(b.entity_id), 'name': b.entity.name,
                       'currency': b.currency} for b in Book.objects.filter(entity__in=entities).select_related('entity').order_by('entity__name', 'id')]}


def receipt_value(row):
    return {'state': 'posted', 'journal_id': str(row.journal_id), 'actor_id': str(row.actor_id),
            'payload_hash': row.payload_hash, 'revision': row.revision, 'at': row.at.isoformat()}


@guarded
@transaction.atomic
def confirm_own_journal(user, book_id, journal_id, value):
    """Human HTTP control only, per answered own-book policy5258. No agent tool.

    This exact-entry confirmation grants no general execution/delegation right
    and does not alter P03's independent approval for delegated operations.
    """
    user, book = owned(user, book_id, True)
    require(book.created_by_id == user.pk, 'ledger_confirmation_denied')
    fields(value, ('expected_revision', 'payload_hash', 'confirm'))
    require(value['confirm'] is True and type(value['expected_revision']) is int)
    require(type(value['payload_hash']) is str and re.fullmatch(r'[a-f0-9]{64}', value['payload_hash']) is not None)
    row = book.journals.filter(pk=identity(journal_id)).first()
    require(row is not None, 'ledger_not_found')
    require(row.payload_hash == value['payload_hash'], 'ledger_review_conflict')
    if row.state == 'posted':
        saved = PostingReceipt.objects.get(journal=row)
        require(saved.actor_id == user.pk and saved.payload_hash == value['payload_hash']
                and saved.observed_revision == value['expected_revision'], 'ledger_review_conflict')
        return receipt_value(saved)
    require(row.state == 'draft')
    lines = list(row.lines.select_related('account').order_by('position'))
    require(2 <= len(lines) <= 100 and [line.position for line in lines] == list(range(len(lines))))
    require(all(line.account.book_id == book.pk and ((line.debit_units > 0 and line.credit_units == 0)
                or (line.credit_units > 0 and line.debit_units == 0)) for line in lines))
    require(sum(line.debit_units for line in lines) == sum(line.credit_units for line in lines) == row.total_units)
    payload = {'book_id': str(book.pk), 'entity_id': str(book.entity_id), 'currency': book.currency,
               'date': row.date.isoformat(), 'description': row.description,
               'source': {'kind': row.source_kind, 'reference': row.source_reference}, 'evidence': row.evidence,
               'lines': [{'account_id': str(line.account_id), 'debit': money(line.debit_units),
                          'credit': money(line.credit_units)} for line in lines]}
    require(digest(payload) == row.payload_hash, 'ledger_review_conflict')
    require(book.period_start <= row.date <= book.period_end, 'ledger_date_outside_period')
    require(not closed(book, row.date), 'ledger_period_closed')
    advance(book, value['expected_revision'])
    require(Journal.objects.filter(pk=row.pk, state='draft', payload_hash=row.payload_hash).update(state='posted') == 1,
            'ledger_review_conflict')
    saved = PostingReceipt.objects.create(journal=row, actor=user,
        observed_revision=value['expected_revision'], revision=book.revision, payload_hash=row.payload_hash)
    event(book, user, 'journal.post', row.pk, row.payload_hash)
    return receipt_value(saved)
