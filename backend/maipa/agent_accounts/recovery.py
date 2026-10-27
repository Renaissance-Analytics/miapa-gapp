"""Trusted pre-HTTP restart recovery under the launcher's exclusive install lock.

The supported launcher owns that lock for its whole life. Each provider consumer
also owns its profile lock until its AppContainer/job closes. Reacquiring BOTH
excludes a live supported consumer; kernel KILL_ON_JOB_CLOSE ends descendants
when the former server dies. Never invoke this from HTTP, chat or model tools.
Temporary capsules are moved intact to private recovery storage, never deleted.
Provider homes/configuration/authentication are not read, moved or reset.
"""
from contextlib import ExitStack
import os
from pathlib import Path
import re
import stat
import uuid

from django.conf import settings
from django.db import DatabaseError, transaction
from django.utils import timezone

from backend.maipa.storage import (InstallationLease, StorageUnavailable, install_lock,
    installation_metadata, physical_chain, prepare_directory)
from backend.maipa.workspace.models import Installation
from backend.maipa.workspace.local_code import protect
from backend.maipa.windows_sandbox import wait_runtime_exit
from .models import Conversation, ProviderProfile, SessionReference
from .profiles import _base, _home, _restore_privacy, ProfileUnavailable


def _unavailable():
    return StorageUnavailable('Connection restart recovery unavailable')


def _runtime(path):
    """Only the generated capsule layout, without aliases or payload reads."""
    if not re.fullmatch(r'worker-[a-z0-9_]{8}', path.name):
        raise _unavailable()
    entries = list(path.iterdir())
    if (not path.is_dir() or {p.name for p in entries} - {'code', 'supervisor', 'workspace'}
            or any(not p.is_dir() for p in entries)):
        raise _unavailable()
    count, size = 0, 0
    def unreadable(_):
        raise _unavailable()
    for parent, directories, files in os.walk(path, followlinks=False, onerror=unreadable):
        for name in (*directories, *files):
            child = physical_chain(Path(parent)/name)
            info = child.lstat()
            count += 1
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise _unavailable()
            if stat.S_ISREG(info.st_mode):
                if info.st_nlink != 1:
                    raise _unavailable()
                size += info.st_size
            if count > 4096 or size > 536_870_912:
                raise _unavailable()


def _private_runtime(path):
    # A move does not remove the old AppContainer's explicit/inherited ACEs.
    # Revalidate first, then protect EVERY generated entry before quarantine.
    _runtime(path)
    entries = [path]
    def unreadable(_):
        raise _unavailable()
    for parent, directories, files in os.walk(path, followlinks=False, onerror=unreadable):
        entries.extend(physical_chain(Path(parent)/name) for name in (*directories,*files))
    for entry in entries:
        protect(entry, directory=entry.is_dir())


def recover_connections(guard):
    data = physical_chain(Path(settings.DATA_ROOT))
    if not isinstance(guard, InstallationLease):
        raise _unavailable()
    guard.revalidate(data)
    storage = installation_metadata(data)['storage_id']
    installation = Installation.objects.filter(slot=1).first()
    installation_id = installation.pk if installation else None
    plans = []
    try:
        with ExitStack() as locks:
            # Validate every primary binding/layout and acquire every profile
            # lock BEFORE moving a capsule or retiring persistent authority.
            for saved in ProviderProfile.objects.select_related('account').order_by('id'):
                if (saved.installation_id != installation_id or str(saved.storage_id) != storage
                        or saved.user_id != saved.account.owner_id or saved.provider != saved.account.provider):
                    raise _unavailable()
                base = _base(saved)
                locks.enter_context(install_lock(base))
                home = _home(saved)
                runtimes = []
                for path in base.iterdir():
                    physical_chain(path)
                    if path.name in ('home', 'binding.json', 'maintenance.lock'):
                        continue
                    _runtime(path)
                    runtimes.append(path)
                plans.append((saved, base, home, runtimes))
            rows = list(Conversation.objects.select_related('account').order_by('id'))
            for row in rows:
                if (row.installation_id != installation_id or str(row.storage_id) != storage
                        or row.user_id != row.account.owner_id):
                    raise _unavailable()
            guard.revalidate(data)
            wait_runtime_exit([path for _, _, _, runtimes in plans for path in runtimes])
            for saved, _, home, runtimes in plans:
                if _home(saved) != home:
                    raise _unavailable()
                for path in runtimes:
                    _runtime(path)
            moved = 0
            for saved, base, home, runtimes in plans:
                for path in runtimes:
                    guard.revalidate(data)
                    # Both final absolute paths are checked within their owned
                    # namespaces before the same-volume atomic directory move.
                    physical_chain(path)
                    if path.parent != base or not path.is_relative_to(data/'providers'):
                        raise _unavailable()
                    _private_runtime(path)
                    destination = prepare_directory(data/'artifacts/runtime-recovery') / uuid.uuid4().hex
                    physical_chain(destination)
                    if destination.parent != data/'artifacts/runtime-recovery' or destination.exists():
                        raise _unavailable()
                    os.rename(path, destination)
                    moved += 1
                if runtimes:
                    _restore_privacy(saved, base, home)
            guard.revalidate(data)
            retired = 0
            with transaction.atomic():
                for row in rows:
                    if not (row.worker or row.ui_id or row.phase in ('connecting', 'sign_in', 'ready', 'thinking', 'stopping')):
                        continue
                    messages = [{**m, 'state': 'interrupted'} if m.get('role') == 'assistant'
                        and m.get('state') == 'streaming' else m for m in row.messages]
                    # Preserve IDs, text, requests and original account/storage;
                    # stale runtime/UI authority cannot survive a server boot.
                    row.messages, row.phase, row.worker, row.heartbeat, row.ui_id = messages, 'offline', None, None, None
                    row.revision += 1
                    row.save(update_fields=['messages','phase','worker','heartbeat','ui','revision'])
                    retired += 1
                SessionReference.objects.filter(installation_id=installation_id,
                    storage_id=storage, revoked_at__isnull=True).update(revoked_at=timezone.now())
            return {'conversations': retired, 'quarantined_runtimes': moved}
    except (OSError, ValueError, TypeError, AttributeError, RecursionError, ProfileUnavailable, DatabaseError):
        raise _unavailable() from None
