"""Private trusted-host persistence. No HTTP route, model tool or provider execution.

An authenticated host supplies its actor independently of the opaque reference.
Saving a protocol handle does not establish that a provider is connected/resumable.
"""
from dataclasses import dataclass, field
from functools import wraps
import hashlib
import hmac
import os
from pathlib import Path
import stat
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.utils import timezone

from backend.maipa.storage import installation_metadata, physical_chain, prepare_directory, regular_file
from backend.maipa.workspace.local_code import protect
from backend.maipa.workspace.models import Membership
from .models import AgentAccount, ArtifactReference, SessionReference

MESSAGE = 'Saved agent resource unavailable'
MAX_PAYLOAD = 1_048_576


class ReferenceUnavailable(Exception):
    """One fixed failure, with no protocol, file, database or authority detail."""


@dataclass(frozen=True)
class SessionHandle:
    provider: str
    value: str = field(repr=False)


@dataclass(frozen=True)
class ArtifactReceipt:
    artifact_id: str
    size: int
    sha256: str


def _safe(operation):
    @wraps(operation)
    def call(*args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except (OSError, ValueError, TypeError, AttributeError, OverflowError, RecursionError, DatabaseError, ObjectDoesNotExist):
            raise ReferenceUnavailable(MESSAGE) from None
    return call


def _uuid(value):
    if type(value) is not str or len(value) != 36 or str(uuid.UUID(value)) != value:
        raise ValueError('Invalid reference')
    return value


def _handle(value):
    if type(value) is not str or not value or not value.isprintable() or len(value.encode('utf-8')) > 4096:
        raise ValueError('Invalid protocol handle')
    return value


def _binding(actor, account_id):
    if not isinstance(actor, get_user_model()) or actor.is_authenticated is not True or actor.pk is None:
        raise ValueError('Actor required')
    account = AgentAccount.objects.get(pk=_uuid(account_id), owner_id=actor.pk,
                                       enabled=True, owner__is_active=True)
    membership = Membership.objects.get(user_id=actor.pk, enabled=True,
        role__in=('administrator', 'preparer'), installation__slot=1)
    storage_id = installation_metadata(settings.DATA_ROOT)['storage_id']
    return (actor.pk, account.pk, membership.installation_id, storage_id, account.provider)


def _session(actor, account_id, reference):
    user, account, installation, storage_id, provider = _binding(actor, account_id)
    saved = SessionReference.objects.get(pk=_uuid(reference), user_id=user, account_id=account,
        installation_id=installation, storage_id=storage_id, provider=provider, revoked_at__isnull=True)
    handle = _handle(saved.private_handle)
    if not hmac.compare_digest(saved.handle_digest, hashlib.sha256(handle.encode('utf-8')).hexdigest()):
        raise ValueError('Private reference damaged')
    return saved


@_safe
def record_session(actor, account_id, private_handle):
    """Record a handle already acknowledged by a trusted protocol host; return app UUID."""
    handle = _handle(private_handle)
    with transaction.atomic():
        user, account, installation, storage_id, provider = _binding(actor, account_id)
        saved = SessionReference.objects.create(user_id=user, account_id=account,
            installation_id=installation, storage_id=storage_id, provider=provider,
            private_handle=handle, handle_digest=hashlib.sha256(handle.encode('utf-8')).hexdigest())
        _session(actor, account_id, str(saved.pk))
    return str(saved.pk)


@_safe
def resolve_session(actor, account_id, reference):
    saved = _session(actor, account_id, reference)
    return SessionHandle(saved.provider, saved.private_handle)


@_safe
def revoke_session(actor, account_id, reference):
    with transaction.atomic():
        saved = _session(actor, account_id, reference)
        if SessionReference.objects.filter(pk=saved.pk, revoked_at__isnull=True).update(revoked_at=timezone.now()) != 1:
            raise ValueError('Reference changed')


def _same(left, right):
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _clean_new(directory, directory_info, path, file_info):
    # Never recurse, follow a replacement, or remove a preexisting collision.
    try:
        physical_chain(directory)
        if directory_info is None or not _same(directory_info, directory.lstat()):
            return
        if file_info is not None:
            regular_file(path)
            if not _same(file_info, path.lstat()):
                return
            path.unlink()
        directory.rmdir()
    except OSError:
        # Private orphans convey no reference/read authority; Q02 owns crash/retention cleanup.
        pass


@_safe
def store_artifact(actor, account_id, reference, payload):
    """Publish a receipt only after exact private bytes are durable and current authority holds."""
    if type(payload) is not bytes or len(payload) > MAX_PAYLOAD:
        raise ValueError('Invalid payload')
    saved = _session(actor, account_id, reference)
    identity = uuid.uuid4()
    root = prepare_directory(Path(settings.DATA_ROOT) / 'artifacts')
    directory = root / identity.hex
    path = directory / 'payload'
    directory_info = file_info = None
    try:
        directory.mkdir(mode=0o700)  # Exclusive: a collision must never be adopted or removed.
        directory_info = directory.lstat()
        prepare_directory(directory)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
        file_info = os.fstat(descriptor)
        with os.fdopen(descriptor, 'wb') as output:
            protect(path, output.fileno())
            if output.write(payload) != len(payload):
                raise OSError('Incomplete private write')
            output.flush()
            os.fsync(output.fileno())
        regular_file(path)
        if not _same(file_info, path.lstat()) or path.stat().st_size != len(payload):
            raise ValueError('Private file changed')
        digest = hashlib.sha256(payload).hexdigest()
        with transaction.atomic():
            current = _session(actor, account_id, reference)
            if current.pk != saved.pk:
                raise ValueError('Reference changed')
            ArtifactReference.objects.create(id=identity, session=current, size=len(payload), sha256=digest)
        return ArtifactReceipt(str(identity), len(payload), digest)
    except BaseException:
        _clean_new(directory, directory_info, path, file_info)
        raise


@_safe
def read_artifact(actor, account_id, reference, artifact_id):
    """Verify physical identity, exact content and fresh authority before returning bytes."""
    saved = _session(actor, account_id, reference)
    artifact = ArtifactReference.objects.get(pk=_uuid(artifact_id), session=saved)
    if not 0 <= artifact.size <= MAX_PAYLOAD:
        raise ValueError('Invalid saved payload')
    path = Path(settings.DATA_ROOT) / 'artifacts' / artifact.pk.hex / 'payload'
    regular_file(path)
    before = path.lstat()
    with path.open('rb') as source:
        opened = os.fstat(source.fileno())
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or not _same(before, opened):
            raise ValueError('Private file changed')
        payload = source.read(MAX_PAYLOAD+1)
        after = os.fstat(source.fileno())
    regular_file(path)
    if (not _same(opened, path.lstat()) or not _same(opened, after) or after.st_nlink != 1
            or len(payload) != artifact.size or after.st_size != artifact.size
            or not hmac.compare_digest(hashlib.sha256(payload).hexdigest(), artifact.sha256)):
        raise ValueError('Private payload damaged')
    _session(actor, account_id, reference)
    return payload
