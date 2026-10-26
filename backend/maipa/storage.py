"""Server-owned local install paths, separate from replaceable application code.

No request, user preference or worker can choose these paths. Directory privacy
precedes file creation; existing links/hardlinks are refused before secret reads.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import uuid

from .workspace.local_code import protect


class StorageUnavailable(OSError):
    """A public-safe launcher failure; never includes paths or private bytes."""


def physical_chain(path):
    """Check every existing ancestor without following a reparse/symlink hop."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise StorageUnavailable('Private installation storage unavailable')
    for item in reversed((path, *path.parents)):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise StorageUnavailable('Private installation storage unavailable')
        if item != path and not stat.S_ISDIR(info.st_mode):
            raise StorageUnavailable('Private installation storage unavailable')
    return path


def data_directory(source, environment=None):
    """Resolve configuration only. Importing settings never writes user storage."""
    environment = os.environ if environment is None else environment
    configured = environment.get('MAIPA_DATA_DIR')
    if configured is None:
        if os.name == 'nt':
            local = environment.get('LOCALAPPDATA')
            if not local:
                # Services/CI deliberately scrub ambient user variables. Ask
                # the OS for this launch identity's paths, never borrow secrets
                # or rely on a profile variable merely to import settings.
                from .windows_sandbox import _Api, _windows_paths
                local = _windows_paths(_Api()).get('LOCALAPPDATA')
            if not local:
                raise StorageUnavailable('Set an absolute private MAIPA_DATA_DIR')
            parent = Path(local)
            path = parent / 'Maipa/data'
        else:
            parent = Path(environment.get('XDG_DATA_HOME') or Path.home() / '.local/share')
            path = parent / 'maipa'
    else:
        if not configured or '\0' in configured:
            raise StorageUnavailable('Set an absolute MAIPA_DATA_DIR outside application source')
        path = Path(configured)
    source = Path(source).resolve()
    physical_chain(path)
    if path == path.parent or path.is_relative_to(source) or source.is_relative_to(path):
        raise StorageUnavailable('Set an absolute MAIPA_DATA_DIR outside application source')
    return path


def prepare_directory(path):
    path = physical_chain(path)
    if path.exists():
        # Applying an inheritable DACL can affect existing descendants. Reject
        # links BEFORE doing that, so no foreign hardlink/junction target changes.
        def unreadable(_):
            raise StorageUnavailable('Private installation storage unavailable')
        for parent, directories, files in os.walk(path, followlinks=False, onerror=unreadable):
            for name in (*directories, *files):
                child = physical_chain(Path(parent) / name)
                info = child.lstat()
                if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                    raise StorageUnavailable('Private installation storage unavailable')
                if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                    raise StorageUnavailable('Private installation storage unavailable')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    physical_chain(path)
    if not path.is_dir():
        raise StorageUnavailable('Private installation storage unavailable')
    protect(path, directory=True)
    return path


def prepare_files(root):
    root = Path(root)
    # Validate all known files before any permission change or database open.
    paths = [root / name for name in ('maipa.sqlite3', 'local-setup-code',
        'maipa.sqlite3-journal', 'maipa.sqlite3-wal', 'maipa.sqlite3-shm')]
    for path in paths:
        physical_chain(path)
        if path.exists():
            private_file(path)
    database = root / 'maipa.sqlite3'
    try:
        fd = os.open(database, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, 'wb') as output:
            protect(database, output.fileno())
    private_file(database)
    metadata = root / 'installation.json'
    if not metadata.exists():
        value = {'format': 'maipa-local-install', 'version': 1, 'storage_id': str(uuid.uuid4())}
        try:
            write_private(metadata, (json.dumps(value, indent=2) + '\n').encode())
        except FileExistsError:
            pass
    installation_metadata(root)


def prepare_install(source):
    path = data_directory(source)
    legacy = Path(source) / 'runtime'
    if any((legacy / name).exists() or (legacy / name).is_symlink()
            for name in ('maipa.sqlite3', 'development-key', 'local-setup-code')):
        try:
            physical_chain(legacy)
            binding = strict_json(legacy / 'install-location.json', 8192)
            metadata = installation_metadata(path)
            if (not isinstance(binding, dict) or set(binding) != {'format', 'version', 'data_directory', 'storage_id'} or
                    binding['format'] != 'maipa-legacy-location' or type(binding['version']) is not int or
                    binding['version'] != 1 or not isinstance(binding['data_directory'], str) or
                    Path(binding['data_directory']) != path or binding['storage_id'] != metadata['storage_id']):
                raise ValueError('Legacy binding mismatch')
        except (OSError, ValueError, TypeError, RecursionError):
            raise StorageUnavailable('Legacy install requires explicit offline adoption before startup') from None
    return prepare_directory(path)


def regular_file(path):
    path = physical_chain(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise StorageUnavailable('Private installation file unavailable')
    return path


def private_file(path):
    path = regular_file(path)
    protect(path)
    return path


def write_private(path, raw):
    """Exclusive create only; never overwrite or publish partial private bytes."""
    path = physical_chain(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as output:
            protect(path, output.fileno())
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def strict_json(path, budget=1_048_576):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate storage metadata field')
            result[key] = value
        return result
    with regular_file(path).open('rb') as source:
        raw = source.read(budget + 1)
    if len(raw) > budget:
        raise StorageUnavailable('Storage metadata budget exceeded')
    return json.loads(raw, object_pairs_hook=unique)


def installation_metadata(root):
    value = strict_json(Path(root) / 'installation.json', 4096)
    if (not isinstance(value, dict) or set(value) != {'format', 'version', 'storage_id'} or
            value['format'] != 'maipa-local-install' or type(value['version']) is not int or
            value['version'] != 1 or not isinstance(value['storage_id'], str) or
            str(uuid.UUID(value['storage_id'])) != value['storage_id']):
        raise StorageUnavailable('Unsupported installation storage format')
    return value


@contextmanager
def install_lock(root):
    """Held by the launcher for its whole life and by offline maintenance."""
    path = physical_chain(Path(root) / 'maintenance.lock')
    try:
        write_private(path, b'')
    except FileExistsError:
        pass
    private_file(path)
    with path.open('r+b') as handle:
        acquired = False
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
            yield
        except OSError:
            raise StorageUnavailable('Installation is active or maintenance is unavailable') from None
        finally:
            if acquired:
                handle.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def development_key(root, generate):
    path = physical_chain(Path(root) / 'development-key')
    if not path.exists():
        if (Path(root) / 'installation.json').exists():
            raise StorageUnavailable('Existing installation signing key unavailable')
        # Generate first: an entropy failure must never publish an empty key.
        value = generate(72)
        if not valid_signing_key(value):
            raise StorageUnavailable('Private development key unavailable')
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as output:
                    protect(path, output.fileno())
                    output.write(value)
                    output.flush()
                    os.fsync(output.fileno())
            except BaseException:
                path.unlink(missing_ok=True)
                raise
    private_file(path)
    with path.open('rb') as source:
        raw = source.read(1025)
    if len(raw) > 1024:
        raise StorageUnavailable('Private development key unavailable')
    value = raw.decode('utf-8')
    if not valid_signing_key(value):
        raise StorageUnavailable('Private development key unavailable')
    return value


def valid_signing_key(value):
    # Preserve ordinary Django key punctuation and exact UTF-8 bytes. Control
    # characters, unbounded files and ambiguous multiline records are invalid.
    return isinstance(value, str) and 50 <= len(value) <= 256 and value.isprintable() and not value.isspace()
