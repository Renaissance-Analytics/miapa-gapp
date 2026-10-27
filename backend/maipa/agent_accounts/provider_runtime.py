"""Install exact public vendor code; credentials and user CLI homes are never consulted."""
import base64
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
import time
import urllib.request

from django.conf import settings

from backend.maipa.storage import install_lock, prepare_directory
from backend.maipa.windows_sandbox import physical_path


def codex_lock():
    path = Path(settings.ACP_RUNTIME_ROOT) / 'codex-runtime.lock.json'
    value = json.loads(path.read_text(encoding='utf-8'))
    if (value['package'] != '@openai/codex' or value['version'] != '0.163.0-win32-x64'
            or len(value['files']) != 47 or value['total_bytes'] != 465693832
            or value['tarball'] != 'https://registry.npmjs.org/@openai/codex/-/codex-0.163.0-win32-x64.tgz'
            or value['entry'] != 'vendor/x86_64-pc-windows-msvc/bin/codex.exe'):
        raise ValueError('Provider runtime unavailable')
    if sum(item['size'] for item in value['files'].values()) != value['total_bytes']:
        raise ValueError('Provider runtime budget')
    for name, item in value['files'].items():
        relative = PurePosixPath(name)
        if (relative.is_absolute() or '..' in relative.parts or '\\' in name or ':' in name or
                type(item['size']) is not int or not 0 <= item['size'] <= value['total_bytes'] or
                not re.fullmatch('[a-f0-9]{64}', item['sha256'])):
            raise ValueError('Provider runtime inventory unavailable')
    return value


def claude_lock():
    value = json.loads((Path(settings.ACP_RUNTIME_ROOT) / 'claude-runtime.lock.json').read_text(encoding='utf-8'))
    if (value['package'] != '@anthropic-ai/claude-code-win32-x64' or value['version'] != '2.1.311'
            or value['entry'] != 'claude.exe' or len(value['files']) != 4 or value['total_bytes'] != 298871002
            or value['tarball'] != 'https://registry.npmjs.org/@anthropic-ai/claude-code-win32-x64/-/claude-code-win32-x64-2.1.311.tgz'):
        raise ValueError('Provider runtime unavailable')
    if sum(item['size'] for item in value['files'].values()) != value['total_bytes']:
        raise ValueError('Provider runtime budget')
    for name, item in value['files'].items():
        relative = PurePosixPath(name)
        if (relative.is_absolute() or '..' in relative.parts or '\\' in name or ':' in name or
                type(item['size']) is not int or not 0 <= item['size'] <= value['total_bytes'] or
                not re.fullmatch('[a-f0-9]{64}', item['sha256'])):
            raise ValueError('Provider runtime inventory unavailable')
    return value


def _verify(directory, lock):
    directory = physical_path(directory)
    actual = {p.relative_to(directory).as_posix() for p in directory.rglob('*') if not p.is_dir()}
    if actual != set(lock['files']):
        raise ValueError('Provider runtime unavailable')
    for name, expected in lock['files'].items():
        path = physical_path(directory / name, directory=False)
        if not path.is_relative_to(directory) or path.stat().st_size != expected['size'] or path.stat().st_nlink != 1:
            raise ValueError('Provider runtime unavailable')
        with path.open('rb') as source:
            if hashlib.file_digest(source, 'sha256').hexdigest() != expected['sha256']:
                raise ValueError('Provider runtime unavailable')
    return directory


def verify_codex(directory):
    return _verify(directory, codex_lock())


def verify_claude(directory):
    return _verify(directory, claude_lock())


def _ensure(cache, version, read_lock, verify, cancelled):
    if os.name != 'nt':
        raise OSError('Provider runtime unavailable')
    # Reproducible public code belongs beside installed dependencies, outside the
    # durable user-data backup boundary. npm ci may discard and rebuild this cache.
    base = prepare_directory(Path(settings.ACP_RUNTIME_ROOT) / 'node_modules' / '.cache' / cache)
    started = time.monotonic()
    def check():
        if cancelled is not None and cancelled():
            raise InterruptedError('Provider preparation cancelled')
        if time.monotonic() - started > 300:
            raise TimeoutError('Provider preparation deadline')
    check()
    with install_lock(base):
        target = base / version
        if target.exists():
            return verify(target)
        lock = read_lock()
        with tempfile.TemporaryDirectory(prefix='download-', dir=base) as temporary:
            temporary = physical_path(temporary)
            archive, digest, total = temporary / 'package.tgz', hashlib.sha512(), 0
            with urllib.request.urlopen(lock['tarball'], timeout=10) as response, archive.open('xb') as output:
                if response.url != lock['tarball']:
                    raise ValueError('Provider download unavailable')
                # read1 returns after one socket read, so trickled bytes cannot
                # postpone the total deadline or a requested cancellation.
                while block := response.read1(65536):
                    check()
                    total += len(block)
                    if total > 300_000_000:
                        raise ValueError('Provider download budget')
                    digest.update(block)
                    output.write(block)
            if base64.b64encode(digest.digest()).decode() != lock['integrity'].removeprefix('sha512-'):
                raise ValueError('Provider integrity unavailable')
            extracted = temporary / 'code'
            extracted.mkdir()
            seen, members = set(), 0
            with tarfile.open(archive, 'r:gz') as package:
                for member in package:
                    members += 1
                    if members > 128 or '\\' in member.name or ':' in member.name:
                        raise ValueError('Provider archive unavailable')
                    name = PurePosixPath(member.name)
                    if name.is_absolute() or '..' in name.parts or not name.parts or name.parts[0] != 'package':
                        raise ValueError('Provider archive unavailable')
                    if member.isdir():
                        continue
                    relative = PurePosixPath(*name.parts[1:]).as_posix()
                    expected = lock['files'].get(relative)
                    if not member.isfile() or relative in seen or expected is None or member.size != expected['size']:
                        raise ValueError('Provider archive unavailable')
                    seen.add(relative)
                    destination = extracted / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with package.extractfile(member) as source, destination.open('xb') as output:
                        shutil.copyfileobj(source, output, length=1048576)
            verify(extracted)
            check()
            extracted.rename(target)
        return verify(target)


def ensure_codex(*, cancelled=None):
    return _ensure('maipa-codex', '0.163.0-win32-x64', codex_lock, verify_codex, cancelled)


def ensure_claude(*, cancelled=None):
    return _ensure('maipa-claude', '2.1.311-win32-x64', claude_lock, verify_claude, cancelled)
