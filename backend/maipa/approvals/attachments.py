"""Text files the person attaches in chat. Owned, bounded and private; the agent reads them only by ID."""
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
    """Human upload only. Plain text up to 512 KiB with a simple file name."""
    _writer(user)
    require(type(value) is dict and set(value) == {'name', 'content'})
    name, content = value['name'], value['content']
    require(type(name) is str and 0 < len(name) <= 128 and name == name.strip() and name not in ('.', '..')
            and re.fullmatch(r'[^/\\:*?"<>|\x00-\x1f\x7f]+', name) is not None)
    require(type(content) is str and content.strip() != '' and not any(ord(c) < 32 and c not in '\n\r\t' for c in content))
    raw = content.encode('utf-8')
    require(len(raw) <= MAX_BYTES, 'attachment_too_large')
    require(ChatAttachment.objects.filter(user=user).count() < MAX_FILES, 'attachment_limit')
    row = ChatAttachment.objects.create(user=user, name=name, content=content, size=len(raw),
                                        sha256=hashlib.sha256(raw).hexdigest(), lines=len(content.splitlines()))
    return _value(row)


def listing(user):
    _writer(user)
    return [_value(row) for row in ChatAttachment.objects.filter(user=user).order_by('-created_at')[:MAX_FILES]]


def read(user, identity):
    """What the agent sees: identity plus a bounded preview."""
    row = _row(user, identity)
    return {**_value(row), 'preview': row.content[:PREVIEW], 'truncated': len(row.content) > PREVIEW}


def content(user, identity):
    row = _row(user, identity)
    return row.name, row.content


def resolve_payload(user, operation, payload):
    """Let a statement import name an attachment; the stored request still holds the exact content."""
    if operation != 'statement.import' or type(payload) is not dict or 'attachment_id' not in payload:
        return payload
    require(set(payload) - {'account_id'} == {'book_id', 'attachment_id'})
    name, text = content(user, payload['attachment_id'])
    return {'book_id': payload['book_id'], 'filename': name, 'content': text,
            **({'account_id': payload['account_id']} if 'account_id' in payload else {})}
