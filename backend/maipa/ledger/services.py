"""Native books: exact four-decimal integer storage and immutable entry proposals.

Proposals have no posting side effect. Posting requires a separate exact-payload
human confirmation under fresh authority; it is never inferred from a draft.
"""
from datetime import date
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

from backend.maipa.intake import services as intake
from .models import Account, Book, CATEGORIES, Journal, JournalLine, LedgerEvent, PostingReceipt, SourceImport, SourceRow

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
    page = list(row.journals.order_by('-sequence')[:101])
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
    return account


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
        require(existing.request_hash == request_hash, 'ledger_operation_conflict')
        return entry_value(existing)
    dated = day(value['date'])
    require(book.period_start <= dated <= book.period_end, 'ledger_date_outside_period')
    require(not closed(book, dated), 'ledger_period_closed')
    description = text(value['description'], 500)
    fields(value['source'], ('kind', 'reference'))
    # Reversal and opening entries are only ever built by their own services.
    require(value['source']['kind'] in ('manual', 'csv') or (_kind in ('reversal', 'opening') and value['source']['kind'] == _kind))
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
        row = SourceRow.objects.filter(pk=row_id, source_import__book=book).first()
        require(row is not None, 'import_row_not_found')
        require(row.date == dated, 'import_date_mismatch')
        require(abs(row.amount_units) == debit, 'import_amount_mismatch')
    require(not book.journals.filter(source_kind=value['source']['kind'], source_reference=reference).exists(),
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
    for line in JournalLine.objects.filter(journal__book=book).select_related('journal').iterator(chunk_size=1000):
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
            'row_count': row.row_count, 'created': created, 'created_at': row.created_at.isoformat()}


@guarded
@transaction.atomic
def import_statement(user, book_id, value):
    """Save a CSV statement's rows as pending source records; no journal is created."""
    user, book = owned(user, book_id, True)
    fields(value, ('expected_revision', 'filename', 'content'))
    filename = text(value['filename'], 128)
    require(re.fullmatch(r'[^/\\:*?"<>|]+', filename) is not None and filename not in ('.', '..'), 'import_file_invalid')
    require(type(value['content']) is str, 'import_file_invalid')
    sha = hashlib.sha256(value['content'].encode('utf-8')).hexdigest()
    existing = book.imports.filter(sha256=sha).first()
    if existing:
        return import_value(existing, False)
    rows = parse_statement(book, value['content'])
    advance(book, value['expected_revision'])
    saved = SourceImport.objects.create(book=book, sha256=sha, filename=filename, row_count=len(rows), imported_by=user)
    for row in rows:
        row.source_import = saved
    SourceRow.objects.bulk_create(rows)
    event(book, user, 'statement.import', saved.pk, sha)
    return import_value(saved, True)


@guarded
@transaction.atomic
def source_rows(user, book_id):
    """Imported statement rows, oldest first, each marked pending or linked to its proposal."""
    _, book = owned(user, book_id)
    cited = {journal.source_reference: journal for journal in book.journals.filter(source_kind='csv')}
    rows = []
    for row in SourceRow.objects.filter(source_import__book=book).select_related('source_import').order_by(
            'date', 'source_import__created_at', 'row_number')[:MAX_IMPORT_ROWS * 5]:
        journal = cited.get(str(row.pk))
        rows.append({'id': str(row.pk), 'import_id': str(row.source_import_id), 'import_filename': row.source_import.filename,
                     'row_number': row.row_number, 'date': row.date.isoformat(), 'description': row.description,
                     'amount': money(row.amount_units), 'reference': row.reference,
                     'status': 'pending' if journal is None else 'posted' if journal.state == 'posted' else 'proposed',
                     'journal_id': None if journal is None else str(journal.pk)})
    return {'book_id': str(book.pk), 'currency': book.currency, 'revision': book.revision, 'rows': rows}

def _statements(book, accounts, start, end):
    """Statements for one range, with their totals in exact units."""
    require(book.period_start <= start <= end <= book.period_end, 'ledger_date_outside_period')
    in_range = dict.fromkeys(accounts, 0)   # debit minus credit inside the range
    to_date = dict.fromkeys(accounts, 0)    # debit minus credit up to the end date
    lines = JournalLine.objects.filter(journal__book=book, journal__state='posted', journal__date__lte=end).select_related('journal')
    for line in lines.iterator(chunk_size=1000):
        net = line.debit_units - line.credit_units
        to_date[line.account_id] += net
        if line.journal.date >= start:
            in_range[line.account_id] += net

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
    totals = {'total_income': total_income, 'total_expenses': total_expenses, 'net_income': total_income - total_expenses,
              'total_assets': total_assets, 'total_liabilities': total_liabilities, 'total_equity': total_equity,
              'earnings_to_date': earnings}
    return {'from': start.isoformat(), 'to': end.isoformat(),
            'income_statement': {'income': income, 'expenses': expenses, 'total_income': money(total_income),
                                 'total_expenses': money(total_expenses), 'net_income': money(total_income - total_expenses)},
            'balance_sheet': {'as_of': end.isoformat(), 'assets': assets, 'liabilities': liabilities, 'equity': equity,
                              'total_assets': money(total_assets), 'total_liabilities': money(total_liabilities),
                              'total_equity': money(total_equity), 'earnings_to_date': money(earnings),
                              'total_liabilities_and_equity': money(total_liabilities + total_equity + earnings)}}, totals


@guarded
@transaction.atomic
def financial_report(user, book_id, value):
    """Income statement for a date range and balance sheet as of its end, from posted entries only.

    With compare_from and compare_to, also the same statements for that second range and the exact
    change in each total: the requested range minus the compared range.
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
    lines = (JournalLine.objects.filter(journal__book=book, account=account).select_related('journal')
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
