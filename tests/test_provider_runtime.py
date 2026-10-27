"""Public code installer outcomes; synthetic archives only, no CLI or network."""
import base64
import hashlib
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from django.test import override_settings
from backend.maipa.agent_accounts import provider_runtime


class ProviderRuntimeTests(unittest.TestCase):
    def test_claude_public_archive_is_isolated_and_cache_tampering_is_refused(self):
        with tempfile.TemporaryDirectory(prefix='maipa-public-claude-') as temporary, override_settings(ACP_RUNTIME_ROOT=Path(temporary).resolve()):
            payload, lock, response = self.archive()
            with patch.object(provider_runtime, 'claude_lock', return_value=lock, create=True), patch.object(provider_runtime.urllib.request, 'urlopen', return_value=response) as download:
                target = provider_runtime.ensure_claude()
                self.assertEqual((target/'vendor/codex.exe').read_bytes(), payload)
                self.assertIn('maipa-claude', str(target))
                self.assertEqual(target.name, '2.1.311-win32-x64')
                self.assertEqual(provider_runtime.ensure_claude(), target)
                self.assertEqual(download.call_count, 1)
                (target/'unapproved-file').write_bytes(b'synthetic foreign bytes')
                with self.assertRaises(ValueError): provider_runtime.verify_claude(target)

    def test_claude_cancellation_never_downloads_or_selects_codex(self):
        with tempfile.TemporaryDirectory(prefix='maipa-public-claude-') as temporary, override_settings(ACP_RUNTIME_ROOT=Path(temporary).resolve()):
            with patch.object(provider_runtime.urllib.request, 'urlopen') as download, patch.object(provider_runtime, 'ensure_codex') as other:
                with self.assertRaises(InterruptedError): provider_runtime.ensure_claude(cancelled=lambda: True)
                download.assert_not_called(); other.assert_not_called()
                self.assertFalse((Path(temporary)/'node_modules/.cache/maipa-claude/2.1.311-win32-x64').exists())

    def archive(self):
        payload = b'synthetic public code, never executable'
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
            item = tarfile.TarInfo('package/vendor/codex.exe')
            item.size = len(payload)
            archive.addfile(item, io.BytesIO(payload))
        wire = buffer.getvalue()
        lock = {'tarball': 'https://registry.npmjs.org/@openai/codex/-/synthetic.tgz',
            'integrity': 'sha512-' + base64.b64encode(hashlib.sha512(wire).digest()).decode(),
            'files': {'vendor/codex.exe': {'size': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}}}
        response = io.BytesIO(wire)
        response.url = lock['tarball']
        return payload, lock, response

    def test_exact_archive_is_published_atomically_and_unchanged_cache_avoids_network(self):
        with tempfile.TemporaryDirectory(prefix='maipa-public-runtime-') as temporary, override_settings(ACP_RUNTIME_ROOT=Path(temporary).resolve()):
            payload, lock, response = self.archive()
            with patch.object(provider_runtime, 'codex_lock', return_value=lock), patch.object(provider_runtime.urllib.request, 'urlopen', return_value=response) as download:
                target = provider_runtime.ensure_codex()
                self.assertEqual((target/'vendor/codex.exe').read_bytes(), payload)
                self.assertEqual(provider_runtime.ensure_codex(), target)
                self.assertEqual(download.call_count, 1)
                self.assertEqual({p.name for p in target.parent.iterdir()}, {'maintenance.lock', '0.163.0-win32-x64'})

    def test_cancellation_before_download_makes_no_network_request_or_published_runtime(self):
        with tempfile.TemporaryDirectory(prefix='maipa-public-runtime-') as temporary, override_settings(ACP_RUNTIME_ROOT=Path(temporary).resolve()):
            with patch.object(provider_runtime.urllib.request, 'urlopen') as download:
                with self.assertRaises(InterruptedError): provider_runtime.ensure_codex(cancelled=lambda: True)
                download.assert_not_called()
                self.assertFalse((Path(temporary)/'node_modules/.cache/maipa-codex/0.163.0-win32-x64').exists())

    def test_corrupt_archive_and_changed_or_extra_cache_bytes_are_refused(self):
        with tempfile.TemporaryDirectory(prefix='maipa-public-runtime-') as temporary, override_settings(ACP_RUNTIME_ROOT=Path(temporary).resolve()):
            payload, lock, response = self.archive()
            lock['integrity'] = 'sha512-' + base64.b64encode(b'0'*64).decode()
            with patch.object(provider_runtime, 'codex_lock', return_value=lock), patch.object(provider_runtime.urllib.request, 'urlopen', return_value=response):
                with self.assertRaises(ValueError): provider_runtime.ensure_codex()
                base = Path(temporary)/'node_modules/.cache/maipa-codex'
                self.assertEqual({p.name for p in base.iterdir()}, {'maintenance.lock'})
                target = base/'0.163.0-win32-x64'
                (target/'vendor').mkdir(parents=True)
                (target/'vendor/codex.exe').write_bytes(payload)
                self.assertEqual(provider_runtime.verify_codex(target), target)
                (target/'foreign-config').write_bytes(b'synthetic unapproved file')
                with self.assertRaises(ValueError): provider_runtime.verify_codex(target)
                (target/'foreign-config').unlink()
                (target/'vendor/codex.exe').write_bytes(b'changed')
                with self.assertRaises(ValueError): provider_runtime.verify_codex(target)

    def test_windows_archive_traversal_is_refused_before_any_escaped_write(self):
        for name in ('package/..\\escape', 'package/C:/escape'):
            with self.subTest(name=name), tempfile.TemporaryDirectory(prefix='maipa-public-runtime-') as temporary, override_settings(ACP_RUNTIME_ROOT=Path(temporary).resolve()):
                payload = b'synthetic hostile archive'
                buffer = io.BytesIO()
                with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
                    item = tarfile.TarInfo(name); item.size = len(payload)
                    archive.addfile(item, io.BytesIO(payload))
                wire = buffer.getvalue()
                lock = {'tarball': 'https://registry.npmjs.org/@openai/codex/-/synthetic.tgz',
                    'integrity': 'sha512-' + base64.b64encode(hashlib.sha512(wire).digest()).decode(),
                    'files': {name.removeprefix('package/'): {'size': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}}}
                response = io.BytesIO(wire); response.url = lock['tarball']
                opened = Path.open
                def guarded(path, *args, **kwargs):
                    if path.name == 'escape': self.fail('Archive escaped its code directory before validation')
                    return opened(path, *args, **kwargs)
                with patch.object(provider_runtime, 'codex_lock', return_value=lock), patch.object(provider_runtime.urllib.request, 'urlopen', return_value=response), patch.object(Path, 'open', guarded):
                    with self.assertRaises(ValueError): provider_runtime.ensure_codex()
                self.assertEqual({p.name for p in (Path(temporary)/'node_modules/.cache/maipa-codex').iterdir()}, {'maintenance.lock'})
