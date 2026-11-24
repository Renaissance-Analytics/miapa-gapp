"""Agent-requested record changes, executed only by the person's exact approval.

The agent records what it wants to change. A human session decides. Approval is
bound to the stored payload hash, and the server then runs the existing domain
service as that person. A failure rolls back and is reported; nothing is partial.
"""
import hashlib
import json
import uuid

from django.db import IntegrityError, transaction
from django.utils import timezone

from backend.maipa.intake import services as intake
from backend.maipa.intake.schema import InvalidFact
from backend.maipa.ledger import services as ledger
from backend.maipa.ledger.models import Book
from .models import ActionRequest

MAX_PAYLOAD_BYTES = 600_000
MAX_PENDING = 200


class ApprovalError(Exception):
    def __init__(self, code='approval_unavailable'):
        self.code = code
        super().__init__(code)


def require(condition, code='invalid_approval_request'):
    if not condition:
        raise ApprovalError(code)


def _identity(value):
    require(type(value) is str)
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise ApprovalError('invalid_approval_request') from None
    require(str(parsed) == value)
    return parsed


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                                     allow_nan=False).encode()).hexdigest()


def _revision(book_id):
    return Book.objects.get(pk=_identity(book_id)).revision


# Each operation: exact payload fields, a read-only check at request time, and the execution.
def _check_book(user, payload):
    ledger.owned(user, payload['book_id'], True)


def _check_import(user, payload):
    _, book = ledger.owned(user, payload['book_id'], True)
    if 'account_id' in payload:
        ledger.import_account(book, payload['account_id'])
    if 'opening_balance' in payload or 'closing_balance' in payload:
        ledger.statement_rows(book, payload)


def _check_books_create(user, payload):
    intake.owned_entity(user, _identity(payload['entity_id']), True)
    require(not Book.objects.filter(entity_id=payload['entity_id']).exists(), 'book_already_initialized')


def _check_post(user, payload):
    _, book = ledger.owned(user, payload['book_id'], True)
    require(book.journals.filter(pk=_identity(payload['journal_id']), state='draft',
                                 payload_hash=payload['payload_hash']).exists(), 'ledger_review_conflict')


def _check_discard(user, payload):
    _, book = ledger.owned(user, payload['book_id'], True)
    require(book.created_by_id == user.pk, 'ledger_confirmation_denied')
    ledger.discard_check(book, payload['journal_id'])


def _check_schedule_create(user, payload):
    _, book = ledger.owned(user, payload['book_id'], True)
    require(book.created_by_id == user.pk, 'ledger_confirmation_denied')
    ledger.schedule_check(book, {key: value for key, value in payload.items() if key != 'book_id'})


def _check_schedule_stop(user, payload):
    _, book = ledger.owned(user, payload['book_id'], True)
    require(book.created_by_id == user.pk, 'ledger_confirmation_denied')
    ledger.stop_check(book, payload['schedule_id'])


def _check_reverse(user, payload):
    _, book = ledger.owned(user, payload['book_id'], True)
    original = book.journals.filter(pk=_identity(payload['journal_id']), state='posted').first()
    require(original is not None, 'ledger_not_found')
    require(original.source_kind != 'reversal', 'ledger_reversal_not_reversible')
    require(not book.journals.filter(source_kind='reversal', source_reference=str(original.pk)).exists(), 'ledger_already_reversed')
    require(book.period_start <= ledger.day(payload['date']) <= book.period_end, 'ledger_date_outside_period')
    require(not ledger.closed(book, ledger.day(payload['date'])), 'ledger_period_closed')


def _check_cash(user, payload):
    _, book = ledger.owned(user, payload['book_id'], True)
    require(book.created_by_id == user.pk, 'approval_denied')
    ledger.cash_change(book, payload['account_id'], payload['cash'])


def _check_match(user, payload):
    _, book = ledger.owned(user, payload['book_id'], True)
    require(book.created_by_id == user.pk, 'approval_denied')
    ledger.match_check(book, payload['row_id'], payload['journal_id'])


def _check_duplicate(user, payload):
    _, book = ledger.owned(user, payload['book_id'], True)
    require(book.created_by_id == user.pk, 'approval_denied')
    ledger.duplicate_check(book, payload['row_id'], payload['of_row_id'])


def _check_statement_account(user, payload):
    _, book = ledger.owned(user, payload['book_id'], True)
    require(book.created_by_id == user.pk, 'approval_denied')
    ledger.statement_account_check(book, payload['import_id'], payload['account_id'])


def _check_balances(user, payload):
    _, book = ledger.owned(user, payload['book_id'], True)
    require(book.created_by_id == user.pk, 'approval_denied')
    ledger.balances_check(book, payload['import_id'], payload['opening_balance'], payload['closing_balance'])


def _check_record(user, payload):
    _, book = ledger.owned(user, payload['book_id'], True)
    require(book.created_by_id == user.pk, 'approval_denied')
    ledger.record_check(book, payload['import_id'])


def _check_opening(user, payload):
    _, book = ledger.owned(user, payload['book_id'], True)
    require(book.created_by_id == user.pk, 'approval_denied')
    ledger.opening_entry(book, {'balances': payload['balances'], 'control': payload['control']})


def _check_period(action):
    def check(user, payload):
        _, book = ledger.owned(user, payload['book_id'], True)
        require(book.created_by_id == user.pk, 'approval_denied')
        ledger.check_period_change(book, action, payload['through'])
    return check


def _run_period(action):
    return lambda user, p: ledger.change_period(user, p['book_id'], {'expected_revision': _revision(p['book_id']),
                                                                    'action': action, 'through': p['through']})


def _check_setup(user, payload):
    from backend.maipa.workspace import onboarding
    row = onboarding.current(user)
    require(str(row.identity) == payload['setup_id'] and row.revision == payload['revision'] and row.step == 'review',
            'setup_review_conflict')


def _run_setup(user, payload):
    from backend.maipa.workspace import onboarding
    row = onboarding.respond(user, {'expected_revision': payload['revision'], 'action': 'reply', 'answer': 'confirm'},
                             payload['setup_id'])
    return {'step': row.step, 'entity_id': str(row.entity_id) if row.entity_id else None}


OPERATIONS = {
    'books.create': (('entity_id', 'currency', 'period_start', 'period_end'), _check_books_create,
        lambda user, p: ledger.create_book(user, _identity(p['entity_id']),
                                           {k: p[k] for k in ('currency', 'period_start', 'period_end')})),
    'account.create': (('book_id', 'code', 'name', 'category'), _check_book,
        lambda user, p: ledger.create_account(user, p['book_id'], {'expected_revision': _revision(p['book_id']),
                                              **{k: p[k] for k in ('code', 'name', 'category')}})),
    'account.cash': (('book_id', 'account_id', 'cash'), _check_cash,
        lambda user, p: ledger.set_account_cash(user, p['book_id'], p['account_id'],
                                                {'expected_revision': _revision(p['book_id']), 'cash': p['cash']})),
    'statement.import': (('book_id', 'filename', 'content'), _check_import,
        lambda user, p: ledger.import_statement(user, p['book_id'], {'expected_revision': _revision(p['book_id']),
            'filename': p['filename'], 'content': p['content'],
            **{key: p[key] for key in ('account_id', 'opening_balance', 'closing_balance') if key in p}})),
    'statement.account': (('book_id', 'import_id', 'account_id'), _check_statement_account,
        lambda user, p: ledger.set_statement_account(user, p['book_id'], {'expected_revision': _revision(p['book_id']),
                                                     'import_id': p['import_id'], 'account_id': p['account_id']})),
    'statement.balances': (('book_id', 'import_id', 'opening_balance', 'closing_balance'), _check_balances,
        lambda user, p: ledger.set_statement_balances(user, p['book_id'], {'expected_revision': _revision(p['book_id']),
            'import_id': p['import_id'], 'opening_balance': p['opening_balance'], 'closing_balance': p['closing_balance']})),
    'reconciliation.record': (('book_id', 'import_id'), _check_record,
        lambda user, p: ledger.record_reconciliation(user, p['book_id'], {'expected_revision': _revision(p['book_id']),
                                                     'import_id': p['import_id']})),
    'statement.match': (('book_id', 'row_id', 'journal_id'), _check_match,
        lambda user, p: ledger.match_row(user, p['book_id'], {'expected_revision': _revision(p['book_id']),
                                         'row_id': p['row_id'], 'journal_id': p['journal_id']})),
    'statement.duplicate': (('book_id', 'row_id', 'of_row_id'), _check_duplicate,
        lambda user, p: ledger.mark_duplicate(user, p['book_id'], {'expected_revision': _revision(p['book_id']),
                                              'row_id': p['row_id'], 'of_row_id': p['of_row_id']})),
    'journal.post': (('book_id', 'journal_id', 'payload_hash'), _check_post,
        lambda user, p: ledger.confirm_own_journal(user, p['book_id'], p['journal_id'],
            {'expected_revision': _revision(p['book_id']), 'payload_hash': p['payload_hash'], 'confirm': True})),
    'journal.reverse': (('book_id', 'journal_id', 'date'), _check_reverse,
        lambda user, p: ledger.reverse_journal(user, p['book_id'], p['journal_id'],
                                               {'expected_revision': _revision(p['book_id']), 'date': p['date']})),
    'journal.discard': (('book_id', 'journal_id'), _check_discard,
        lambda user, p: ledger.discard_journal(user, p['book_id'], p['journal_id'], {'expected_revision': _revision(p['book_id'])})),
    'schedule.create': (('book_id', 'description', 'lines', 'day', 'starts'), _check_schedule_create,
        lambda user, p: ledger.create_schedule(user, p['book_id'], {'expected_revision': _revision(p['book_id']),
                                               **{k: v for k, v in p.items() if k != 'book_id'}})),
    'schedule.stop': (('book_id', 'schedule_id'), _check_schedule_stop,
        lambda user, p: ledger.stop_schedule(user, p['book_id'], {'expected_revision': _revision(p['book_id']), 'schedule_id': p['schedule_id']})),
    'opening.balance': (('book_id', 'balances', 'control'), _check_opening,
        lambda user, p: ledger.post_opening(user, p['book_id'], {'expected_revision': _revision(p['book_id']),
                                            'balances': p['balances'], 'control': p['control']})),
    'period.close': (('book_id', 'through'), _check_period('close'), _run_period('close')),
    'period.reopen': (('book_id', 'through'), _check_period('reopen'), _run_period('reopen')),
    'setup.confirm': (('setup_id', 'revision'), _check_setup, _run_setup),
}
# Fields an operation accepts but does not require.
OPTIONAL = {'statement.import': ('account_id', 'opening_balance', 'closing_balance'), 'schedule.create': ('ends',)}
DOMAIN_ERRORS = (ledger.LedgerError, intake.ScopeDenied, intake.NotFound, intake.RevisionConflict, InvalidFact,
                 ValueError, TypeError, KeyError, IntegrityError, Book.DoesNotExist)


def _code(problem):
    return getattr(problem, 'code', None) or type(problem).__name__.lower()[:64]


def _detail(row):
    """Server-read facts about the target, so the person reviews real records, not only the agent's words."""
    try:
        if row.operation == 'journal.post':
            from backend.maipa.ledger.models import Journal
            journal = Journal.objects.select_related('book').get(pk=row.payload['journal_id'], book_id=row.payload['book_id'])
            return {'date': journal.date.isoformat(), 'description': journal.description, 'currency': journal.book.currency,
                    'reference': journal.source_reference, 'current': journal.payload_hash == row.payload['payload_hash'],
                    'lines': [{'account': f'{line.account.code} {line.account.name}', 'debit': ledger.money(line.debit_units),
                               'credit': ledger.money(line.credit_units)}
                              for line in journal.lines.select_related('account').order_by('position')]}
        if row.operation in ('schedule.create', 'schedule.stop'):
            book = Book.objects.select_related('entity').get(pk=row.payload['book_id'])
            if row.operation == 'schedule.stop':
                saved = book.schedules.get(pk=row.payload['schedule_id'])
                return {'books': book.entity.name, 'description': saved.description, 'day': saved.day, 'starts': saved.starts.isoformat(),
                        'ends': saved.ends.isoformat() if saved.ends else None, 'active': saved.stopped_at is None}
            checked = ledger.schedule_check(book, {key: value for key, value in row.payload.items() if key != 'book_id'})
            names = {str(a.pk): f'{a.code} {a.name}' for a in book.accounts.all()}
            return {'books': book.entity.name, 'currency': book.currency, 'description': checked['description'], 'day': checked['day'],
                    'starts': checked['starts'].isoformat(), 'ends': checked['ends'].isoformat() if checked['ends'] else None,
                    'total': ledger.money(checked['total']),
                    'lines': [{'account': names[l['account_id']], 'debit': l['debit'], 'credit': l['credit']} for l in checked['lines']]}
        if row.operation == 'journal.discard':
            from backend.maipa.ledger.models import Journal
            journal = Journal.objects.select_related('book').get(pk=row.payload['journal_id'], book_id=row.payload['book_id'])
            return {'date': journal.date.isoformat(), 'description': journal.description, 'currency': journal.book.currency,
                    'current': journal.state == 'draft',
                    'lines': [{'account': f'{line.account.code} {line.account.name}', 'debit': ledger.money(line.debit_units),
                               'credit': ledger.money(line.credit_units)}
                              for line in journal.lines.select_related('account').order_by('position')]}
        if row.operation == 'journal.reverse':
            from backend.maipa.ledger.models import Journal
            journal = Journal.objects.select_related('book').get(pk=row.payload['journal_id'], book_id=row.payload['book_id'])
            names = {str(a.pk): f'{a.code} {a.name}' for a in journal.book.accounts.all()}
            return {'date': row.payload['date'], 'description': 'Reversal of: ' + journal.description, 'currency': journal.book.currency,
                    'original_date': journal.date.isoformat(), 'current': journal.state == 'posted',
                    'lines': [{'account': names[l['account_id']], 'debit': l['debit'], 'credit': l['credit']} for l in ledger.reversal_lines(journal)]}
        if row.operation == 'statement.duplicate':
            from backend.maipa.ledger.models import SourceRow
            pair = {str(item.pk): item for item in SourceRow.objects.select_related('source_import__account', 'source_import__book__entity').filter(
                pk__in=(row.payload['row_id'], row.payload['of_row_id']), source_import__book_id=row.payload['book_id'])}
            line, original = pair[row.payload['row_id']], pair[row.payload['of_row_id']]
            book, bank = line.source_import.book, line.source_import.account

            def shown(item):
                return {'statement': item.source_import.filename, 'date': item.date.isoformat(), 'description': item.description,
                        'amount': ledger.money(item.amount_units)}
            return {'books': book.entity.name, 'currency': book.currency, 'account': f'{bank.code} {bank.name}' if bank else None,
                    'row': shown(line), 'of': shown(original)}
        if row.operation in ('statement.balances', 'reconciliation.record'):
            from backend.maipa.ledger.models import SourceImport
            statement = SourceImport.objects.select_related('book__entity', 'account').get(pk=row.payload['import_id'], book_id=row.payload['book_id'])
            book, account = statement.book, statement.account
            facts = {'books': book.entity.name, 'currency': book.currency, 'statement': statement.filename,
                     'account': f'{account.code} {account.name}' if account else None}
            if row.operation == 'statement.balances':
                net = sum(statement.rows.values_list('amount_units', flat=True))
                return {**facts, 'row_count': statement.row_count, 'net': ledger.money(net),
                        'opening_balance': ledger.money(ledger.balance_units(row.payload['opening_balance'])),
                        'closing_balance': ledger.money(ledger.balance_units(row.payload['closing_balance'])),
                        'current': statement.closing_units is None}
            report = ledger._report(book, statement)
            return {**facts, 'as_of': report['statement']['as_of'], 'closing_balance': report['statement']['closing_balance'],
                    'book_balance': report['book_balance'], 'unrecorded_total': report['unrecorded_total'],
                    'outstanding_total': report['outstanding_total'], 'difference': report['difference'], 'reconciled': report['reconciled']}
        if row.operation == 'statement.account':
            from backend.maipa.ledger.models import Account, SourceImport
            statement = SourceImport.objects.select_related('book__entity').get(pk=row.payload['import_id'], book_id=row.payload['book_id'])
            account = Account.objects.get(pk=row.payload['account_id'], book_id=row.payload['book_id'])
            return {'books': statement.book.entity.name, 'statement': statement.filename, 'row_count': statement.row_count,
                    'account': f'{account.code} {account.name}', 'current': statement.account_id is None}
        if row.operation == 'statement.match':
            from backend.maipa.ledger.models import Journal, SourceRow
            line = SourceRow.objects.select_related('source_import__account', 'source_import__book__entity').get(
                pk=row.payload['row_id'], source_import__book_id=row.payload['book_id'])
            journal = Journal.objects.get(pk=row.payload['journal_id'], book_id=row.payload['book_id'])
            book, bank = line.source_import.book, line.source_import.account
            return {'books': book.entity.name, 'currency': book.currency,
                    'row': {'date': line.date.isoformat(), 'description': line.description, 'amount': ledger.money(line.amount_units),
                            'account': f'{bank.code} {bank.name}' if bank else None},
                    'entry': {'sequence': journal.sequence, 'date': journal.date.isoformat(), 'description': journal.description, 'state': journal.state},
                    'lines': [{'account': f'{item.account.code} {item.account.name}', 'debit': ledger.money(item.debit_units),
                               'credit': ledger.money(item.credit_units)} for item in journal.lines.select_related('account').order_by('position')]}
        if row.operation == 'account.cash':
            from backend.maipa.ledger.models import Account
            account = Account.objects.select_related('book__entity').get(pk=row.payload['account_id'], book_id=row.payload['book_id'])
            return {'books': account.book.entity.name, 'account': f'{account.code} {account.name}', 'cash': account.cash}
        if row.operation == 'opening.balance':
            book = Book.objects.select_related('entity').get(pk=row.payload['book_id'])
            facts = {'books': book.entity.name, 'currency': book.currency}
            try:
                built = ledger.opening_entry(book, {'balances': row.payload['balances'], 'control': row.payload['control']})
            except ledger.LedgerError:
                return {**facts, 'current': False}
            return {**facts, 'date': built['date'], 'current': True, 'total_debit': built['total_debit'], 'total_credit': built['total_credit'],
                    'control_debit': built['control_debit'], 'control_credit': built['control_credit'],
                    'lines': [{key: line[key] for key in ('account', 'debit', 'credit', 'offset')} for line in built['lines']]}
        if row.operation in ('period.close', 'period.reopen'):
            book = Book.objects.select_related('entity').get(pk=row.payload['book_id'])
            return {'books': book.entity.name, 'closed_through': book.closed_through.isoformat() if book.closed_through else None}
        if row.operation in ('account.create', 'statement.import'):
            book = Book.objects.select_related('entity').get(pk=row.payload['book_id'])
            facts = {'books': book.entity.name, 'currency': book.currency}
            if row.operation == 'statement.import':
                named = book.accounts.filter(pk=row.payload['account_id']).first() if 'account_id' in row.payload else None
                facts['account'] = f'{named.code} {named.name}' if named else None
                if 'opening_balance' in row.payload:
                    # A reconciled statement: show the rows and both balances for review.
                    rows, opening, closing = ledger.statement_rows(book, row.payload)
                    facts.update(opening_balance=ledger.money(opening), closing_balance=ledger.money(closing),
                                 net=ledger.money(closing - opening), row_count=len(rows),
                                 rows=[{'date': item.date.isoformat(), 'description': item.description,
                                        'amount': ledger.money(item.amount_units)} for item in rows[:60]])
            return facts
        if row.operation == 'books.create':
            from backend.maipa.intake.models import Entity
            return {'entity': Entity.objects.get(pk=row.payload['entity_id']).name}
    except Exception:  # Detail is display help only; a missing target is reported at decision time.
        return None
    return None


def _value(row):
    payload = dict(row.payload)
    if 'content' in payload:  # Never echo a whole statement; identify it exactly instead.
        content = payload.pop('content')
        payload.update(content_bytes=len(content.encode('utf-8')),
                       content_sha256=hashlib.sha256(content.encode('utf-8')).hexdigest(),
                       content_preview=content[:2000])
    return {'id': str(row.pk), 'operation': row.operation, 'summary': row.summary, 'payload': payload,
            'payload_hash': row.payload_hash, 'batch_id': str(row.batch) if row.batch else None, 'state': row.state,
            'detail': _detail(row) if row.state == 'pending' else None, 'result': row.result, 'error': row.error, 'created_at': row.created_at.isoformat(),
            'decided_at': row.decided_at.isoformat() if row.decided_at else None}


def _writer(user):
    try:
        return intake.scope(user, True)
    except (intake.ScopeDenied, intake.NotFound, AttributeError):
        raise ApprovalError('approval_denied') from None


@transaction.atomic
def request_action(user, value):
    """Agent or person records an exact change for the person to decide. Changes no records."""
    _writer(user)
    require(type(value) is dict and set(value) - {'batch_id'} == {'operation', 'payload', 'summary'})
    operation, payload, summary = value['operation'], value['payload'], value['summary']
    require(type(operation) is str and operation in OPERATIONS, 'approval_operation_unsupported')
    fields, check, _ = OPERATIONS[operation]
    require(type(payload) is dict and set(payload) - set(OPTIONAL.get(operation, ())) == set(fields))
    require(len(json.dumps(payload, allow_nan=False).encode()) <= MAX_PAYLOAD_BYTES)
    require(type(summary) is str and 0 < len(summary.strip()) and len(summary) <= 500
            and not any(ord(c) < 32 or ord(c) == 127 for c in summary))
    batch = _identity(value['batch_id']) if value.get('batch_id') is not None else None
    require(ActionRequest.objects.filter(user=user, state='pending').count() < MAX_PENDING, 'approval_limit')
    try:
        check(user, payload)
    except ApprovalError:
        raise
    except DOMAIN_ERRORS as problem:
        raise ApprovalError(_code(problem)) from None
    row = ActionRequest.objects.create(user=user, operation=operation, payload=payload, payload_hash=_digest(
        {'operation': operation, 'payload': payload}), summary=summary.strip(), batch=batch)
    return _value(row)


def list_requests(user):
    rows = ActionRequest.objects.filter(user=user)
    return {'actor_id': str(user.pk),
            'pending': [_value(r) for r in rows.filter(state='pending').order_by('created_at')[:MAX_PENDING]],
            'recent': [_value(r) for r in rows.exclude(state='pending').order_by('-decided_at')[:20]]}


def _decide(user, row, decision):
    if row.state != 'pending':
        require((decision, row.state) in (('approve', 'approved'), ('approve', 'failed'), ('reject', 'rejected')),
                'approval_already_decided')
        return _value(row)
    now = timezone.now()
    if decision == 'reject':
        require(ActionRequest.objects.filter(pk=row.pk, state='pending').update(state='rejected', decided_at=now) == 1,
                'approval_already_decided')
    else:
        try:
            with transaction.atomic():
                require(ActionRequest.objects.filter(pk=row.pk, state='pending').update(state='approved', decided_at=now) == 1,
                        'approval_already_decided')
                result = OPERATIONS[row.operation][2](user, row.payload)
                ActionRequest.objects.filter(pk=row.pk).update(result=result)
        except ApprovalError:
            raise
        except DOMAIN_ERRORS as problem:
            ActionRequest.objects.filter(pk=row.pk, state='pending').update(state='failed', error=_code(problem), decided_at=now)
    row.refresh_from_db()
    return _value(row)


def decide(user, request_id, value):
    """Human control only: approve (execute exactly) or reject one request."""
    _writer(user)
    require(type(value) is dict and set(value) == {'decision', 'payload_hash'} and value['decision'] in ('approve', 'reject'))
    row = ActionRequest.objects.filter(pk=_identity(request_id), user=user).first()
    require(row is not None, 'approval_not_found')
    require(value['payload_hash'] == row.payload_hash, 'approval_review_conflict')
    return _decide(user, row, value['decision'])


def decide_batch(user, batch_id, value):
    """Human control only: decide every pending request of one batch; each reports its own outcome."""
    _writer(user)
    require(type(value) is dict and set(value) == {'decision', 'payload_hashes'} and value['decision'] in ('approve', 'reject'))
    rows = list(ActionRequest.objects.filter(user=user, batch=_identity(batch_id)).order_by('created_at'))
    require(rows, 'approval_not_found')
    hashes = value['payload_hashes']
    require(type(hashes) is dict and {str(r.pk): r.payload_hash for r in rows if r.state == 'pending'} == hashes,
            'approval_review_conflict')
    return {'batch_id': str(batch_id), 'requests': [_decide(user, row, value['decision']) for row in rows]}
