"""Private original-account homes and exclusive trusted-consumer leases.

This is not an HTTP/tool surface. The consumer must revalidate before each real
operation and keep the lease until its OS worker and descendants are confirmed
stopped. Nothing here reads credential bytes, grants a worker ACL or claims login.
"""
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import stat

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction

from backend.maipa.acp import Scope
from backend.maipa.storage import (install_lock, physical_chain, prepare_directory,
                                  strict_json, write_private)
from backend.maipa.workspace.local_code import protect
from .models import ProviderProfile
from .references import _binding, _uuid

MESSAGE = 'Agent profile unavailable'
FAILURES = (OSError, ValueError, TypeError, AttributeError, OverflowError,
            RecursionError, DatabaseError, ObjectDoesNotExist)


class ProfileUnavailable(Exception):
    """No private profile, authority or filesystem diagnostics cross the boundary."""


def _values(saved):
    return (saved.user_id, saved.account_id, saved.installation_id,
            str(saved.storage_id), saved.provider)


def _lookup(actor, account_id, profile_id):
    binding = _binding(actor, account_id)
    saved = ProviderProfile.objects.get(pk=_uuid(profile_id), account_id=binding[1])
    if _values(saved) != binding:
        raise ValueError('Original profile binding changed')
    return saved


def _base(saved):
    root = physical_chain(Path(settings.DATA_ROOT) / 'providers')
    path = physical_chain(root / saved.id.hex)
    if not path.is_dir():
        raise ValueError('Profile directory unavailable')
    return path


def _marker(saved):
    return {'format': 'maipa-provider-profile', 'version': 1, 'id': str(saved.pk),
            'user_id': str(saved.user_id), 'account_id': str(saved.account_id),
            'installation_id': str(saved.installation_id), 'storage_id': str(saved.storage_id),
            'provider': saved.provider}


def _home(saved):
    base = _base(saved)
    marker = strict_json(base / 'binding.json', 4096)
    if marker != _marker(saved) or type(marker.get('version')) is not int:
        raise ValueError('Original profile layout changed')
    home = physical_chain(base / 'home')
    if not home.is_dir():
        raise ValueError('Assigned home unavailable')
    # Metadata only, before any inheritable DACL change. A provider may create
    # files, but cannot use a junction/hardlink to extend its next worker grant.
    count, size = 0, 0
    def unreadable(_):
        raise ValueError('Profile tree unavailable')
    for parent, directories, files in os.walk(home, followlinks=False, onerror=unreadable):
        for name in (*directories, *files):
            child = physical_chain(Path(parent) / name)
            info = child.lstat()
            count += 1
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise ValueError('Unsupported profile entry')
            if stat.S_ISREG(info.st_mode):
                if info.st_nlink != 1:
                    raise ValueError('Profile file alias')
                size += info.st_size
            if count > 4096 or size > 536_870_912:
                raise ValueError('Profile storage budget')
    return home


def _retained_layout(base):
    # An unremoved capsule after a crash/failed stop is not permission to start
    # a second consumer. Explicit offline recovery owns such orphan cleanup.
    if {path.name for path in base.iterdir()} != {'home', 'binding.json', 'maintenance.lock'}:
        raise ValueError('Unfinished profile consumer')


def _restore_privacy(saved, base, home):
    if _home(saved) != home or _base(saved) != base:
        raise ValueError('Profile layout changed')
    _retained_layout(base)
    # Validate the WHOLE tree before changing any DACL, including the lock and
    # marker. SetFileSecurityW on a parent alone does not remove all previously
    # inherited ACEs from retained child files; protect each validated entry.
    prepare_directory(base)
    entries = [base, home, base / 'binding.json', base / 'maintenance.lock']
    def unreadable(_):
        raise ValueError('Profile privacy cleanup unavailable')
    for parent, directories, files in os.walk(home, followlinks=False, onerror=unreadable):
        entries.extend(physical_chain(Path(parent) / name) for name in (*directories, *files))
    for path in entries:
        protect(path, directory=path.is_dir())


def provision_profile(actor, account_id):
    """Reserve an immutable original binding; never repair/adopt existing homes."""
    try:
        with transaction.atomic():
            user, account, installation, storage_id, provider = _binding(actor, account_id)
            saved, _ = ProviderProfile.objects.get_or_create(account_id=account,
                defaults={'user_id': user, 'installation_id': installation,
                          'storage_id': storage_id, 'provider': provider})
            saved = _lookup(actor, account_id, str(saved.pk))
        root = physical_chain(Path(settings.DATA_ROOT) / 'providers')
        if not root.exists():
            prepare_directory(root)
        base = physical_chain(root / saved.id.hex)
        try:
            base.mkdir(mode=0o700)
        except FileExistsError:
            # Existing private state is accepted only after exact marker and
            # current-authority checks under the same exclusive native lease.
            with lease_profile(actor, account_id, str(saved.pk)):
                pass
        else:
            prepare_directory(base)
            # Complete only our newly created directory. A crash before this
            # marker/home is complete stays unavailable; never adopt its bytes.
            write_private(base / 'binding.json', json.dumps(_marker(saved), sort_keys=True).encode())
            prepare_directory(base / 'home')
            with lease_profile(actor, account_id, str(saved.pk)):
                pass
        return str(saved.pk)
    except FAILURES:
        raise ProfileUnavailable(MESSAGE) from None


@dataclass(frozen=True)
class ProfileLease:
    scope: Scope
    provider: str
    home: Path = field(repr=False)
    _check: object = field(repr=False, compare=False)
    _closed: bool = field(default=False, repr=False, compare=False)

    def revalidate(self):
        try:
            if self._closed:
                raise ValueError('Profile lease closed')
            self._check()
        except FAILURES:
            raise ProfileUnavailable(MESSAGE) from None


@contextmanager
def lease_profile(actor, account_id, profile_id):
    """Hold the real OS lock through verified consumer shutdown, including failures."""
    stack = ExitStack()
    try:
        saved = _lookup(actor, account_id, profile_id)
        base = _base(saved)
        # Reuse the installation service's fail-closed cross-process native
        # lock primitive within this private profile directory, not DATA_ROOT.
        stack.enter_context(install_lock(base))
        _retained_layout(base)
        home = _home(saved)
        original = _values(saved)
        def check():
            current = _lookup(actor, account_id, profile_id)
            if _values(current) != original or _home(current) != home:
                raise ValueError('Profile changed')
        lease = ProfileLease(Scope(str(saved.user_id), str(saved.account_id)), saved.provider, home, check)
        lease.revalidate()
    except FAILURES + (ProfileUnavailable,):
        stack.close()
        raise ProfileUnavailable(MESSAGE) from None
    try:
        yield lease
    finally:
        object.__setattr__(lease, '_closed', True)
        # close() passes no consumer exception into the lock context. Consumer
        # failures propagate unchanged; they are not storage error diagnostics.
        try:
            try:
                # Profile deletion alone leaves the old AppContainer SID's ACEs
                # on retained directories. Only after the consumer has stopped
                # its worker, validate every alias before restoring private
                # inheritable DACLs. Never repair links or delete profile bytes.
                _restore_privacy(saved, base, home)
            finally:
                stack.close()
        except FAILURES:
            raise ProfileUnavailable(MESSAGE) from None
