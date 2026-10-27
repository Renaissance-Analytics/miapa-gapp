"""Durable conversation operations; no model-selected principal, paths or commands."""
from datetime import timedelta
import json
import uuid

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from backend.maipa.workspace.models import Membership
from .models import AgentAccount, Conversation
from .ui import UiUnavailable, ui_snapshot
from .references import _binding, ReferenceUnavailable


class ChatUnavailable(Exception):
    pass


class ChatConflict(ChatUnavailable):
    pass


def authority(actor):
    if not actor.is_authenticated or not actor.is_active:
        raise ChatUnavailable('Conversation unavailable')
    try:
        return Membership.objects.select_related('installation').get(user_id=actor.pk,
            user__is_active=True, enabled=True, role__in=('administrator', 'preparer'), installation__slot=1)
    except ObjectDoesNotExist:
        raise ChatUnavailable('Conversation unavailable') from None


def _account(actor, *, create=False):
    authority(actor)
    if create:
        account, _ = AgentAccount.objects.get_or_create(owner=actor, provider='codex',
                                                       defaults={'label': 'ChatGPT subscription'})
    else:
        account = AgentAccount.objects.filter(owner=actor, provider='codex').first()
    if account and not account.enabled:
        raise ChatUnavailable('Conversation unavailable')
    return account


def _state(actor, row, *, refresh_stale_ui=False):
    if row is None:
        return {'id': None, 'revision': 0, 'phase': 'offline', 'messages': [], 'ui': None}
    ui = None
    if row.ui_id:
        try:
            ui = {'account_id': str(row.account_id), **ui_snapshot(actor, str(row.account_id), str(row.ui_id))}
        except UiUnavailable:
            if refresh_stale_ui:
                raise
            pass  # A revoked composition is removed; its contents never reach the browser.
    return {'id': str(row.pk), 'revision': row.revision, 'phase': row.phase,
            'messages': row.messages, 'ui': ui}


def _conversation(actor, account, *, create=False):
    try:
        user, account_id, installation, storage, provider = _binding(actor, str(account.pk))
        if provider != 'codex':
            raise ChatUnavailable('Conversation unavailable')
        if create:
            row, _ = Conversation.objects.get_or_create(account=account, defaults={
                'user_id': user, 'installation_id': installation, 'storage_id': storage})
        else:
            row = Conversation.objects.filter(account=account).first()
        if row and (row.user_id, row.account_id, row.installation_id, str(row.storage_id)) != (user, account_id, installation, storage):
            raise ChatUnavailable('Conversation unavailable')
        return row
    except (ReferenceUnavailable, ObjectDoesNotExist, ValueError, OSError):
        raise ChatUnavailable('Conversation unavailable') from None


def snapshot(actor):
    account = _account(actor)
    from .chat_runtime import refresh_actor
    for attempt in range(2):
        refresh_actor(actor)
        row = _conversation(actor, account) if account else None
        try:
            return _state(actor, row, refresh_stale_ui=attempt == 0)
        except UiUnavailable:
            # A committed unrelated epoch can race the actor check above.
            # Reauthorize once from primary state before publishing absence;
            # otherwise a healthy shared form would briefly unmount and lose drafts.
            continue
    return _state(actor, row)


def start_worker(actor, row, session_key):
    # Lazy import keeps persistence tests independent of native process startup.
    from .chat_runtime import start
    try:
        start(actor, row, session_key)
    except ChatUnavailable:
        # Capacity refusal occurs before a worker can own cleanup. Expose a
        # recoverable connection state while retaining the acknowledged request.
        # Never overwrite another worker's live state.
        Conversation.objects.filter(pk=row.pk, worker__isnull=True, phase='connecting').update(
            phase='unavailable', revision=F('revision') + 1)
        raise


def _revision(value):
    if type(value) is not int or not 0 <= value < 1_999_999_999:
        raise ChatUnavailable('Conversation unavailable')


def send(actor, revision, request_id, text, *, session_key=None):
    _revision(revision)
    if (type(request_id) is not str or str(uuid.UUID(request_id)) != request_id or
            type(text) is not str or not text.strip() or len(text) > 8000 or
            any(ord(char) < 32 and char not in '\n\t' for char in text)):
        raise ValueError('Invalid message')
    account = _account(actor, create=True)
    with transaction.atomic():
        row = _conversation(actor, account, create=True)
        Conversation.objects.filter(pk=row.pk).update(revision=F('revision'))
        row.refresh_from_db()
        if request_id in row.requests:
            existing = next((m for m in row.messages if m['id'] == request_id), None)
            if existing is None or existing['text'] != text:
                raise ChatConflict('Conversation changed')
            return _state(actor, row)
        if row.revision != revision or row.phase in ('connecting', 'sign_in', 'thinking', 'stopping'):
            raise ChatConflict('Conversation changed')
        if len(row.messages) >= 126 or len(json.dumps(row.messages).encode()) + len(text.encode()) > 196608:
            raise ChatUnavailable('Conversation storage limit reached')
        row.messages.append({'id': request_id, 'role': 'user', 'text': text, 'state': 'queued'})
        row.requests.append(request_id)
        row.revision += 1
        row.phase = 'thinking' if row.worker else 'connecting'
        row.save(update_fields=['messages', 'requests', 'revision', 'phase'])
    start_worker(actor, row, session_key)
    return snapshot(actor)


def connect(actor, revision, *, session_key):
    _revision(revision)
    account = _account(actor, create=True)
    with transaction.atomic():
        row = _conversation(actor, account, create=True)
        Conversation.objects.filter(pk=row.pk).update(revision=F('revision'))
        row.refresh_from_db()
        if row.revision != revision or row.phase in ('connecting', 'sign_in', 'thinking', 'stopping'):
            raise ChatConflict('Conversation changed')
        row.phase, row.revision = 'connecting', row.revision + 1
        row.save(update_fields=['phase', 'revision'])
    start_worker(actor, row, session_key)
    return snapshot(actor)


def control(actor, action, *, session_key):
    account = _account(actor)
    if not account:
        raise ChatUnavailable('Conversation unavailable')
    from .chat_runtime import control as worker_control
    worker_control(actor, account, action, session_key)
    return snapshot(actor)


def worker_update(actor, identity, token, **changes):
    """Only the originally claimed worker may publish a stream checkpoint."""
    authority(actor)
    row = Conversation.objects.select_related('account').get(pk=identity, user_id=actor.pk, account__owner_id=actor.pk, worker=token)
    _conversation(actor, row.account)
    if set(changes) - {'messages', 'phase', 'ui_id'}:
        raise ValueError('Invalid checkpoint')
    changes.update(revision=F('revision') + 1, heartbeat=timezone.now())
    if Conversation.objects.filter(pk=row.pk, worker=token).update(**changes) != 1:
        raise ChatUnavailable('Conversation unavailable')


def claim_worker(actor, identity):
    authority(actor)
    token = uuid.uuid4()
    now = timezone.now()
    with transaction.atomic():
        row = Conversation.objects.select_related('account').get(pk=identity, user_id=actor.pk, account__owner_id=actor.pk)
        _conversation(actor, row.account)
        if row.worker and row.heartbeat and row.heartbeat > now - timedelta(seconds=20):
            raise ChatConflict('Conversation already connected')
        if Conversation.objects.filter(pk=row.pk, worker=row.worker).update(worker=token, heartbeat=now) != 1:
            raise ChatConflict('Conversation already connected')
    return token
