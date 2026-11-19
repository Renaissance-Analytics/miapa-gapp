"""Files the person attaches in chat. Owned, bounded and private; the agent reads them only by ID."""
import base64
import binascii
import hashlib
import re
import uuid

from django.db import transaction

from backend.maipa.intake import services as intake
from .models import ChatAttachment

MAX_BYTES = 524_288
MAX_FILES = 100
PREVIEW = 4000


class AttachmentError(Exception):
    def __init__(self, code='attachment_unavailable'):
        self.code = code
        super().__init__(code)


def require(condition, code='invalid_attachment'):
    if not condition:
        raise AttachmentError(code)


def _writer(user):
    try:
        return intake.scope(user, True)
    except (intake.ScopeDenied, intake.NotFound, AttributeError):
        raise AttachmentError('attachment_denied') from None


def _value(row):
    return {'id': str(row.pk), 'name': row.name, 'size': row.size, 'sha256': row.sha256, 'lines': row.lines,
            'created_at': row.created_at.isoformat()}


def _row(user, identity):
    _writer(user)
    require(type(identity) is str)
    try:
        parsed = uuid.UUID(identity)
    except ValueError:
        raise AttachmentError('attachment_not_found') from None
    row = ChatAttachment.objects.filter(pk=parsed, user=user).first()
    require(row is not None and str(parsed) == identity, 'attachment_not_found')
    return row


@transaction.atomic
def store(user, value):
    """Human upload only. Plain text, or an Excel statement workbook as base64, up to 512 KiB with a simple file name."""
    _writer(user)
    require(type(value) is dict and set(value) in ({'name', 'content'}, {'name', 'content_base64'}))
    name = value['name']
    require(type(name) is str and 0 < len(name) <= 128 and name == name.strip() and name not in ('.', '..')
            and re.fullmatch(r'[^/\\:*?"<>|\x00-\x1f\x7f]+', name) is not None)
    original = None
    if 'content_base64' in value:
        # An Excel workbook: kept exactly, and converted once to the rows the importer checks.
        kind = name.lower().rsplit('.', 1)[-1]
        require(kind in ('xlsx', 'pdf') and type(value['content_base64']) is str and value['content_base64'] != '')
        try:
            raw = base64.b64decode(value['content_base64'], validate=True)
        except (binascii.Error, ValueError):
            raise AttachmentError('invalid_attachment') from None
        from backend.maipa.ledger import documents, services as ledger, spreadsheet
        require(0 < len(raw) <= (documents.MAX_DOCUMENT_BYTES if kind == 'pdf' else MAX_BYTES), 'attachment_too_large')
        try:
            # A workbook becomes the rows the importer checks; a PDF becomes its text for the agent to read.
            content = documents.pdf_text(raw) if kind == 'pdf' else spreadsheet.statement_csv(raw)
        except ledger.LedgerError as problem:
            raise AttachmentError(problem.code) from None
        original = raw
    else:
        content = value['content']
        require(type(content) is str and content.strip() != '' and not any(ord(c) < 32 and c not in '\n\r\t' for c in content))
        raw = content.encode('utf-8')
        require(len(raw) <= MAX_BYTES, 'attachment_too_large')
    require(ChatAttachment.objects.filter(user=user).count() < MAX_FILES, 'attachment_limit')
    row = ChatAttachment.objects.create(user=user, name=name, content=content, original=original, size=len(raw),
                                        sha256=hashlib.sha256(raw).hexdigest(), lines=len(content.splitlines()))
    return _value(row)


def listing(user):
    _writer(user)
    return [_value(row) for row in ChatAttachment.objects.filter(user=user).order_by('-created_at')[:MAX_FILES]]


def _kind(row):
    return None if row.original is None else row.name.lower().rsplit('.', 1)[-1]


def read(user, identity, offset=0):
    """What the agent sees: identity plus a bounded window of the text, starting at offset."""
    row = _row(user, identity)
    require(type(offset) is int and 0 <= offset <= len(row.content))
    return {**_value(row), 'preview': row.content[offset:offset + PREVIEW], 'truncated': offset + PREVIEW < len(row.content),
            'offset': offset, 'length': len(row.content), 'converted_from': _kind(row)}


def content(user, identity):
    row = _row(user, identity)
    return row.name, row.content


def resolve_payload(user, operation, payload):
    """Let a statement import name an attachment; the stored request still holds the exact content."""
    if operation != 'statement.import' or type(payload) is not dict or 'attachment_id' not in payload:
        return payload
    row = _row(user, payload['attachment_id'])
    extras = ('account_id', 'opening_balance', 'closing_balance')
    if _kind(row) != 'pdf':
        require(set(payload) - set(extras) == {'book_id', 'attachment_id'})
        return {'book_id': payload['book_id'], 'filename': row.name, 'content': row.content,
                **{key: payload[key] for key in extras if key in payload}}
    # A PDF has no table: the agent supplies the rows it read, and they must reconcile to the document itself.
    require(set(payload) == {'book_id', 'attachment_id', 'content', *extras})
    from backend.maipa.ledger import documents, services as ledger
    try:
        _, book = ledger.owned(user, payload['book_id'], True)
        rows, opening, closing = ledger.statement_rows(book, payload)
        documents.require_written(row.content, [opening, closing, *(item.amount_units for item in rows)])
    except ledger.LedgerError as problem:
        raise AttachmentError(problem.code) from None
    return {'book_id': payload['book_id'], 'filename': row.name, 'content': payload['content'],
            **{key: payload[key] for key in extras}}
