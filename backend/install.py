"""Offline local-operator snapshots; never an HTTP, chat or agent tool surface.

Stop the managed site before maintenance. Snapshots contain private credentials
and records and are not encrypted exports. Existing destinations are never replaced.
"""
import argparse
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import tempfile
import time
import uuid

from backend.maipa.storage import (StorageUnavailable, data_directory,
    install_lock, installation_metadata, physical_chain, prepare_directory,
    private_file, regular_file, strict_json, valid_signing_key, write_private)

CODE = Path(__file__).resolve().parents[1]
ROOT_FILES = {'maipa.sqlite3', 'development-key', 'local-setup-code', 'installation.json'}
NAMESPACES = {'providers', 'sessions', 'artifacts'}
EPHEMERAL = {'maintenance.lock', 'maipa.sqlite3-journal', 'maipa.sqlite3-wal', 'maipa.sqlite3-shm'}
MANIFEST_BUDGET = 32 * 1_048_576
DEVICES = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{n}' for n in range(1, 10)), *(f'LPT{n}' for n in range(1, 10))}


def unavailable():
    return StorageUnavailable('Offline installation maintenance unavailable')


def configured(path):
    return data_directory(CODE, {'MAIPA_DATA_DIR': str(path)})


def allowed_name(name):
    if not isinstance(name, str) or len(name) > 2048 or not re.fullmatch(r'[A-Za-z0-9_./-]+', name):
        raise unavailable()
    parts = name.split('/')
    if (len(parts) > 32 or any(not part or part in {'.', '..'} or part.endswith('.') or
            len(part) > 200 or part.split('.')[0].upper() in DEVICES for part in parts) or
            PurePosixPath(name).is_absolute()):
        raise unavailable()
    if name not in ROOT_FILES and not (len(parts) >= 2 and parts[0] in NAMESPACES):
        raise unavailable()
    return name


def inventory(root, *, snapshot=False):
    root = physical_chain(root)
    if not root.is_dir():
        raise unavailable()
    result = {}
    def failed(_):
        raise unavailable()
    for parent, directories, files in os.walk(root, followlinks=False, onerror=failed):
        for name in list(directories):
            path = physical_chain(Path(parent) / name)
            relative = path.relative_to(root)
            if len(relative.parts) == 1:
                if name == 'workers' and not snapshot:
                    directories.remove(name)
                    continue
                if name not in NAMESPACES:
                    raise unavailable()
            else:
                allowed_name(relative.as_posix() + '/directory-check')
        for name in files:
            path = regular_file(Path(parent) / name)
            relative = path.relative_to(root).as_posix()
            if relative == 'snapshot.json' and snapshot:
                continue
            if relative in EPHEMERAL and not snapshot:
                continue
            allowed_name(relative)
            result[relative] = path
            if len(result) > 100_000:
                raise unavailable()
    if not {'maipa.sqlite3', 'development-key', 'installation.json'} <= result.keys():
        raise unavailable()
    if len({name.casefold() for name in result}) != len(result):
        raise unavailable()
    installation_metadata(root)
    return result


def file_record(path):
    digest, size = hashlib.sha256(), 0
    with regular_file(path).open('rb') as source:
        while raw := source.read(1_048_576):
            digest.update(raw)
            size += len(raw)
    return {'bytes': size, 'sha256': digest.hexdigest()}


def validate_key(root):
    with regular_file(Path(root) / 'development-key').open('rb') as source:
        raw = source.read(1025)
    if len(raw) > 1024 or not valid_signing_key(raw.decode('utf-8')):
        raise unavailable()


def copy_private(source, destination):
    source, destination = regular_file(source), physical_chain(destination)
    prepare_directory(destination.parent)
    # Exclusive destination, protected before the first copied byte.
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    from backend.maipa.workspace.local_code import protect
    try:
        with os.fdopen(fd, 'wb') as output, source.open('rb') as incoming:
            protect(destination, output.fileno())
            shutil.copyfileobj(incoming, output, 1_048_576)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


@contextmanager
def staging(destination):
    destination = configured(destination)
    physical_chain(destination.parent)
    if destination.exists() or destination.is_symlink() or not destination.parent.is_dir():
        raise unavailable()
    stage = Path(tempfile.mkdtemp(prefix='.maipa-stage-', dir=destination.parent))
    try:
        prepare_directory(stage)
        yield stage
        if destination.exists() or destination.is_symlink():
            raise unavailable()
        # Windows rename refuses an existing destination, including empty dirs.
        # Non-Windows install/recovery execution remains unverified.
        if os.name != 'nt':
            raise StorageUnavailable('Offline maintenance requires verified Windows support')
        os.rename(stage, destination)
    finally:
        if stage.exists():
            physical_chain(stage)
            if stage.parent != destination.parent or not stage.name.startswith('.maipa-stage-'):
                raise unavailable()
            shutil.rmtree(stage)


def database_check(connection):
    if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise unavailable()
    if connection.execute('PRAGMA foreign_key_check').fetchall():
        raise unavailable()
    required = {'django_migrations', 'auth_user', 'workspace_bootstrapstate', 'workspace_membership'}
    actual = {row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
    if not required <= actual:
        raise unavailable()


@contextmanager
def frozen_database(path):
    # IMMEDIATE excludes independent writers; backup reads through a separate
    # connection so the lock holder's write transaction cannot stall backup().
    path = regular_file(path)
    # SQLite opens these names itself. Validate them before giving SQLite even
    # the primary filename; a later check is too late for a redirected WAL.
    for suffix in ('-journal', '-wal', '-shm'):
        sidecar = physical_chain(path.with_name(path.name + suffix))
        if sidecar.exists():
            regular_file(sidecar)
    with closing(sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=0,
            isolation_level=None)) as writer:
        try:
            writer.execute('BEGIN IMMEDIATE')
            yield
        finally:
            writer.rollback()


def database_snapshot(source, destination):
    write_private(destination, b'')
    deadline = time.monotonic() + 15
    def progress(status, *_):
        if status not in {sqlite3.SQLITE_OK, sqlite3.SQLITE_DONE} or time.monotonic() > deadline:
            raise unavailable()
    with closing(sqlite3.connect(regular_file(source).as_uri() + '?mode=ro', uri=True, timeout=0)) as incoming:
        with closing(sqlite3.connect(destination)) as output:
            output.execute('PRAGMA synchronous=FULL')
            incoming.backup(output, pages=256, progress=progress, sleep=0)
            output.execute('PRAGMA journal_mode=DELETE')
            database_check(output)
            output.commit()


def distinct(source, destination):
    source, destination = configured(source), configured(destination)
    if source.is_relative_to(destination) or destination.is_relative_to(source):
        raise unavailable()
    return source, destination


def backup(source, destination):
    source, destination = distinct(source, destination)
    # Recognize/validate first: a mistyped source must not acquire or modify an
    # unrelated folder's lockfile. Repeat under the real lock before copying.
    inventory(source)
    with install_lock(source):
        files = inventory(source)
        with frozen_database(files['maipa.sqlite3']), staging(destination) as stage:
            database_snapshot(files['maipa.sqlite3'], stage / 'maipa.sqlite3')
            for name, path in sorted(files.items()):
                if name != 'maipa.sqlite3':
                    copy_private(path, stage / name)
            validate_key(stage)
            records = {name: file_record(stage / name) for name in sorted(files)}
            manifest = {'format': 'maipa-local-snapshot', 'version': 1, 'files': records}
            raw = (json.dumps(manifest, indent=2) + '\n').encode()
            if len(raw) > MANIFEST_BUDGET:
                raise unavailable()
            write_private(stage / 'snapshot.json', raw)
    return len(files)


def restore(source, destination):
    source, destination = distinct(source, destination)
    manifest = strict_json(source / 'snapshot.json', MANIFEST_BUDGET)
    if (not isinstance(manifest, dict) or set(manifest) != {'format', 'version', 'files'} or
            manifest['format'] != 'maipa-local-snapshot' or type(manifest['version']) is not int or
            manifest['version'] != 1 or not isinstance(manifest['files'], dict) or
            not 1 <= len(manifest['files']) <= 100_000):
        raise unavailable()
    for name, record in manifest['files'].items():
        allowed_name(name)
        if (not isinstance(record, dict) or set(record) != {'bytes', 'sha256'} or
                type(record['bytes']) is not int or record['bytes'] < 0 or
                not isinstance(record['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', record['sha256'])):
            raise unavailable()
    files = inventory(source, snapshot=True)
    if files.keys() != manifest['files'].keys():
        raise unavailable()
    for name, path in files.items():
        if file_record(path) != manifest['files'][name]:
            raise unavailable()
    with staging(destination) as stage:
        for name, path in sorted(files.items()):
            copy_private(path, stage / name)
            if file_record(stage / name) != manifest['files'][name]:
                raise unavailable()
        installation_metadata(stage)
        validate_key(stage)
        with closing(sqlite3.connect((stage / 'maipa.sqlite3').as_uri() + '?mode=ro', uri=True, timeout=0)) as database:
            database_check(database)
    return len(files)


def adopt_legacy(source, destination):
    """Adopt the known pre-P05 SQLite/key layout with the old site stopped.

    Legacy diagnostics/capsules remain in the original checkout. Provider/session
    directories were never supported there; their presence requires investigation
    instead of silently omitting private state. Original data files are retained.
    """
    source, destination = physical_chain(source), configured(destination)
    if not source.is_dir() or source.is_relative_to(destination) or destination.is_relative_to(source):
        raise unavailable()
    legacy = physical_chain(source / 'runtime')
    marker = physical_chain(legacy / 'install-location.json')
    if marker.exists() or destination.exists() or not legacy.is_dir():
        raise unavailable()
    if any((legacy / name).exists() or (legacy / name).is_symlink() for name in NAMESPACES):
        raise unavailable()
    files = {name: regular_file(legacy / name) for name in ('maipa.sqlite3', 'development-key')}
    if (legacy / 'local-setup-code').exists() or (legacy / 'local-setup-code').is_symlink():
        files['local-setup-code'] = regular_file(legacy / 'local-setup-code')
    validate_key(legacy)
    bound, published, binding_raw = False, False, None
    try:
        with frozen_database(files['maipa.sqlite3']):
            for path in files.values():
                private_file(path)
            for name in ('maipa.sqlite3-journal', 'maipa.sqlite3-wal', 'maipa.sqlite3-shm'):
                path = legacy / name
                if path.exists() or path.is_symlink():
                    private_file(path)
            with staging(destination) as stage:
                database_snapshot(files['maipa.sqlite3'], stage / 'maipa.sqlite3')
                for name, path in sorted(files.items()):
                    if name != 'maipa.sqlite3':
                        copy_private(path, stage / name)
                metadata = {'format': 'maipa-local-install', 'version': 1, 'storage_id': str(uuid.uuid4())}
                write_private(stage / 'installation.json', (json.dumps(metadata, indent=2) + '\n').encode())
                validate_key(stage)
                binding = {'format': 'maipa-legacy-location', 'version': 1,
                    'data_directory': str(destination), 'storage_id': metadata['storage_id']}
                binding_raw = (json.dumps(binding, indent=2) + '\n').encode()
                # Publish the private binding while the destination is still
                # hidden. A failed rename removes only this exact owned record.
                write_private(marker, binding_raw)
                bound = True
            published = True
    except BaseException:
        if bound and not published:
            with regular_file(marker).open('rb') as handle:
                current = handle.read(8193)
            if current != binding_raw:
                raise unavailable() from None
            marker.unlink()
        raise
    return len(files) + 1


def main(argv=None):
    class PrivateParser(argparse.ArgumentParser):
        def error(self, message):
            self.exit(2, 'Invalid offline maintenance command\n')
    parser = PrivateParser(description=__doc__)
    parser.add_argument('operation', choices=['backup', 'restore', 'adopt-legacy'])
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--destination', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        count = {'backup': backup, 'restore': restore, 'adopt-legacy': adopt_legacy}[args.operation](args.source, args.destination)
    except (OSError, ValueError, TypeError, RecursionError, sqlite3.Error):
        print(json.dumps({'status': 'unavailable', 'operation': args.operation}))
        return 1
    print(json.dumps({'status': 'complete', 'operation': args.operation, 'files': count}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
