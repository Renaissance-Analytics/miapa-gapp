"""Original ACP-session-bound bookkeeping. Proposals are never posting authority."""
import json

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction

from backend.maipa.agent_accounts import ui
from . import services

NAMES = frozenset(('maipa_books_list', 'maipa_books_read', 'maipa_books_activity', 'maipa_books_imports', 'maipa_journal_propose'))


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
        {'name': 'maipa_books_imports', 'description': 'Read statement rows the person imported into an owned book, oldest first, each pending, proposed or posted. To categorize a pending row, propose a journal with source kind csv and the row id as reference, the row date, and a total equal to the row amount. You cannot import files.', 'inputSchema': obj({'book_id': identifier})},
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
            else:
                services.fields(arguments, ('book_id', 'entry') if name == 'maipa_journal_propose'
                               else ('book_id', 'account_id') if name == 'maipa_books_activity' else ('book_id',))
                book_id = arguments['book_id']
                if name == 'maipa_books_imports':
                    value = services.source_rows(actor, book_id)
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
