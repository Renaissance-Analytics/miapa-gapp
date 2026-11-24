"""Original ACP-session-bound bookkeeping. Proposals are never posting authority."""
import json

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction

from backend.maipa.agent_accounts import ui
from . import services

NAMES = frozenset(('maipa_books_list', 'maipa_books_read', 'maipa_books_activity', 'maipa_books_imports', 'maipa_books_report', 'maipa_books_verify', 'maipa_books_reconcile', 'maipa_books_close_review', 'maipa_books_schedules', 'maipa_books_prepare_due', 'maipa_books_releases', 'maipa_books_groups', 'maipa_journal_propose'))


def tool_definitions():
    def obj(properties, required=None):
        return {'type': 'object', 'properties': properties,
                'required': list(properties) if required is None else required, 'additionalProperties': False}
    identifier = {'type': 'string', 'format': 'uuid'}
    amount = {'type': 'string', 'pattern': r'^(0|[1-9][0-9]{0,13})(\.[0-9]{1,4})?$'}
    entry = obj({'operation_id': identifier, 'expected_revision': {'type': 'integer', 'minimum': 0},
        'date': {'type': 'string', 'format': 'date'}, 'description': {'type': 'string', 'minLength': 1, 'maxLength': 500},
        'source': obj({'kind': {'type': 'string', 'enum': ['manual', 'csv']},
                       'reference': {'type': 'string', 'minLength': 1, 'maxLength': 256}}),
        'evidence': {'type': 'array', 'minItems': 1, 'maxItems': 20,
                     'items': {'type': 'string', 'minLength': 1, 'maxLength': 256}},
        'lines': {'type': 'array', 'minItems': 2, 'maxItems': 100,
                   'items': obj({'account_id': identifier, 'debit': amount, 'credit': amount})}})
    return [
        {'name': 'maipa_books_list', 'description': 'Read current owned business/entity book metadata. No setup, credentials or financial writes.', 'inputSchema': obj({})},
        {'name': 'maipa_books_read', 'description': 'Read an owned book chart, recent immutable journal proposals and actual posted trial balance. Read before proposing; distinguish pending from posted.', 'inputSchema': obj({'book_id': identifier})},
        {'name': 'maipa_books_activity', 'description': 'Read one owned account\'s posted entries in date order with exact running balances, plus pending (unposted) entries listed separately. Read-only.', 'inputSchema': obj({'book_id': identifier, 'account_id': identifier})},
        {'name': 'maipa_books_imports', 'description': 'Read statement rows the person imported into an owned book, oldest first, each pending, proposed or posted. Each row has account_id, the bank account its statement belongs to, or null. To categorize a pending row, propose a journal with source kind csv and the row id as reference and the row date. When account_id is set, the entry must debit that account by exactly the row amount for money in, or credit it for money out; other lines may carry more, so a payment-processor payout is ONE entry: debit the bank for the net received, debit fees, credit sales for the gross. When account_id is null the entry total must equal the row amount. A row whose entry was later reversed is pending again but keeps that source entry: propose the corrected entry as a manual entry (same date, the bank account moved by the row amount) and request statement.match for the row. transfer_candidates lists pairs of pending rows that look like one transfer between two bank accounts (money out of one, the same amount into another within ten days): propose ONE entry for the out row that debits the receiving bank account and credits the paying one, then request statement.match for the in row and that entry. entry_candidates lists pending rows an existing entry already records: request statement.match instead of a second entry. duplicate_candidates lists pending rows that repeat a row of an earlier statement of the same bank account (same date, amount and wording, as when two statements overlap): request statement.duplicate for each instead of an entry. Each row reports via: entry, match, duplicate or null, and duplicate_of. You cannot import files.', 'inputSchema': obj({'book_id': identifier})},
        {'name': 'maipa_books_verify', 'description': 'Recheck every stored entry of one book against the double-entry rules: each entry balances, its lines are intact and in this book, posted entries have their posting record, reversals mirror their original and the change history is complete. Read-only. ok is true only when findings is empty; report any finding to the person exactly and do not try to repair it.', 'inputSchema': obj({'book_id': identifier})},
        {'name': 'maipa_books_close_review', 'description': 'Month-end close review, read-only: what stands between the books and closing them through a date (YYYY-MM-DD, inside the book period). Returns the calendar of months (closed or open) and four checks with their outstanding items: drafts dated on or before the date, statement lines up to the date without a posted entry, bank statements ending in the period being closed that are not reconciled as they stand, and integrity findings. can_close is the hard rule (no drafts in the period, date after the last close); ready also needs every check clear. Use it before requesting period.close and tell the person plainly what is outstanding; it changes nothing and decides nothing.', 'inputSchema': obj({'book_id': identifier, 'through': {'type': 'string', 'format': 'date'}}, ['book_id', 'through'])},
        {'name': 'maipa_books_schedules', 'description': 'Read-only: the recurring entries of a book (approved templates: description, day of the month, start and optional end, lines, whether still active) with the occurrences due on or before a date (YYYY-MM-DD, inside the book period) that have no entry yet, and the next later date. Closed months and discarded drafts are accounted for.', 'inputSchema': obj({'book_id': identifier, 'through': {'type': 'string', 'format': 'date'}}, ['book_id', 'through'])},
        {'name': 'maipa_books_prepare_due', 'description': 'Draft every due recurring entry on or before a date, at most once per occurrence. Drafts are proposals that change no balance; posting each still needs the person to approve journal.post (request them together in one batch). Use maipa_books_schedules first and tell the person what you are drafting.', 'inputSchema': obj({'book_id': identifier, 'through': {'type': 'string', 'format': 'date'}}, ['book_id', 'through'])},
        {'name': 'maipa_books_releases', 'description': 'Read-only: the released statements of a book, newest first. A release is the income statement, balance sheet and cash flow of a closed period, kept exactly as the person approved releasing them. Each has unchanged (the books still give the same figures today) and latest (no newer release of the same range). With release_id: the kept statements and every total that differs today (changes). To release statements request report.release with maipa_action_request; this tool cannot.',
         'inputSchema': obj({'book_id': identifier, 'release_id': identifier}, ['book_id'])},
        {'name': 'maipa_books_groups', 'description': 'Read-only. With no arguments: the person\'s entity groups (books read together) with their member books. With group_id, from and to (YYYY-MM-DD): the combined income statement, balance sheet and cash totals for that range, each row with one amount per member book in the listed order and their total. consolidated (null until the person approves accounts with group.eliminate) gives the totals without those accounts, each removed amount, and income_difference and balance_difference, which are zero when both sides recorded every dealing. eliminated is true only then; otherwise say the totals are not, or not fully, adjusted for dealings between the entities. To create, change or remove a group request group.create, group.eliminate or group.remove with maipa_action_request; this tool cannot.',
         'inputSchema': obj({'group_id': identifier, 'from': {'type': 'string', 'format': 'date'}, 'to': {'type': 'string', 'format': 'date'}}, [])},
        {'name': 'maipa_books_reconcile', 'description': 'Bank reconciliation. With only book_id: list the imported statements with their bank account, stated opening and closing balance, dates and can_reconcile. With import_id: compare that statement\'s closing balance with the books at its last date. unrecorded_lines are statement lines not yet posted; outstanding_entries are posted entries on the account that no statement line has cleared; reconciled is true only when books plus unrecorded lines equal the bank balance plus outstanding entries. Report the exact difference and what explains it; never adjust a figure to make it reconcile. A statement needs a bank account and both balances.', 'inputSchema': obj({'book_id': identifier, 'import_id': identifier}, ['book_id'])},
        {'name': 'maipa_books_report', 'description': 'Read the income statement and cash flow for a date range and the balance sheet as of its end date, from posted entries only (exact decimal strings). cash_flow gives the opening and closing balance of each account marked as a bank or cash account, and what moved cash in the range by the other account of each entry (positive received, negative paid); transfers between marked accounts and unpaid bills move no cash; it is empty when no account is marked. cash_flow.bridge explains why cash changed by a different amount than profit: net_income, plus not_in_cash (income and expenses not yet received or paid), plus other_accounts (cash moved by asset, liability and equity accounts), equals net_change. With a comparison, change.net_cash_change is the difference in the change in cash. Dates must be inside the book period. Use this for profit, totals and balances; never compute them yourself. To compare two periods (for example this month against last month), also give compare_from and compare_to together: the result adds the same statements for that range and the exact change in each total, requested range minus compared range.', 'inputSchema': obj({'book_id': identifier, 'from': {'type': 'string', 'format': 'date'}, 'to': {'type': 'string', 'format': 'date'}, 'compare_from': {'type': 'string', 'format': 'date'}, 'compare_to': {'type': 'string', 'format': 'date'}}, ['book_id', 'from', 'to'])},
        {'name': 'maipa_journal_propose', 'description': 'Save an exact balanced journal proposal for the currently visible Maipa.Books component. Requires the just-read book revision and original account IDs. Use decimal strings, original user-supplied references and a stable operation UUID. This never posts; the human reviews the exact entry in the shared UI.', 'inputSchema': obj({'book_id': identifier, 'entry': entry})},
    ]


class LedgerBroker:
    def __init__(self, actor, account_id, identity):
        self.actor_id = actor.pk
        self.account_id, self.identity = account_id, identity
        self.observed = {}

    @transaction.atomic
    def call(self, name, arguments):
        try:
            actor = get_user_model().objects.get(pk=self.actor_id, is_active=True)
            original = ui._lookup(actor, self.account_id, self.identity)
            services.require(name in NAMES and type(arguments) is dict)
            services.require(len(json.dumps(arguments, allow_nan=False).encode()) <= 65536)
            if name == 'maipa_books_list':
                services.fields(arguments, ())
                value = services.list_books(actor)
            elif name == 'maipa_books_groups':
                from . import groups
                if arguments:
                    services.fields(arguments, ('group_id', 'from', 'to'))
                    value = groups.combined_report(actor, arguments['group_id'], {'from': arguments['from'], 'to': arguments['to']})
                else:
                    value = groups.list_groups(actor)
            else:
                services.fields(arguments, ('book_id', 'entry') if name == 'maipa_journal_propose'
                               else ('book_id', 'account_id') if name == 'maipa_books_activity'
                               else ('book_id', 'from', 'to', *(key for key in ('compare_from', 'compare_to') if key in arguments))
                               if name == 'maipa_books_report'
                               else ('book_id', *(('import_id',) if 'import_id' in arguments else ()))
                               if name == 'maipa_books_reconcile' else ('book_id', *(('release_id',) if 'release_id' in arguments else ()))
                               if name == 'maipa_books_releases' else ('book_id', 'through') if name in ('maipa_books_close_review', 'maipa_books_schedules', 'maipa_books_prepare_due') else ('book_id',))
                book_id = arguments['book_id']
                if name == 'maipa_books_report':
                    value = services.financial_report(actor, book_id, {key: item for key, item in arguments.items() if key != 'book_id'})
                elif name == 'maipa_books_reconcile':
                    value = (services.reconciliation(actor, book_id, {'import_id': arguments['import_id']}) if 'import_id' in arguments
                             else services.statements(actor, book_id))
                elif name == 'maipa_books_releases':
                    value = services.report_releases(actor, book_id, {key: item for key, item in arguments.items() if key != 'book_id'})
                elif name == 'maipa_books_schedules':
                    value = services.schedules(actor, book_id, {'through': arguments['through']})
                elif name == 'maipa_books_prepare_due':
                    value = services.prepare_due(actor, book_id, {'through': arguments['through']})
                elif name == 'maipa_books_close_review':
                    value = services.close_review(actor, book_id, {'through': arguments['through']})
                elif name == 'maipa_books_verify':
                    value = services.verify_book(actor, book_id)
                elif name == 'maipa_books_imports':
                    value = services.source_rows(actor, book_id)
                    found = services.match_candidates(actor, book_id)
                    value = {**value, 'transfer_candidates': found['transfers'], 'entry_candidates': found['entries'],
                             'duplicate_candidates': found['duplicates']}
                elif name == 'maipa_books_activity':
                    value = services.account_activity(actor, book_id, arguments['account_id'])
                elif name == 'maipa_books_read':
                    book = services.read_book(actor, book_id)
                    self.observed[book_id] = book['revision']
                    value = {'book': book, 'report': services.journal_preview(actor, book_id)}
                else:
                    visible = []
                    def visit(node):
                        if node['type'] == 'Maipa.Books':
                            visible.append(node.get('props', {}).get('book_id'))
                        for child in node.get('children', []):
                            visit(child)
                    services.require(original.composition is not None)
                    visit(original.composition['schema'])
                    services.require(book_id in visible and book_id in self.observed)
                    services.require(type(arguments['entry']) is dict and
                                     arguments['entry'].get('expected_revision') == self.observed[book_id])
                    services.require(arguments['entry'].get('source', {}).get('kind') in ('manual', 'csv'))
                    value = services.propose_journal(actor, book_id, arguments['entry'])
                    self.observed.pop(book_id, None)
            return {'content': [{'type': 'text', 'text': json.dumps(value, allow_nan=False)}]}
        except (services.LedgerError, ui.UiUnavailable, ValueError, TypeError, AttributeError,
                KeyError, ObjectDoesNotExist, DatabaseError):
            return {'content': [{'type': 'text', 'text': 'Bookkeeping changed or is unavailable. Read the current book and review before trying again.'}], 'isError': True}
