"""Offline backup/restore must preserve real synthetic Django authority and files."""
import json
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests.test_install_storage import descriptor

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/storage_install.py'


def environment(data):
    allowed = {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'COMSPEC', 'PATHEXT',
        'USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'SYSTEMDRIVE'}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update(MAIPA_DATA_DIR=str(data), MAIPA_SECRET_KEY='synthetic-signing-key-' * 4,
        MAIPA_ALLOWED_HOSTS='testserver', MAIPA_STORAGE_FIXTURE='1')
    return env


class InstallRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = tempfile.TemporaryDirectory(prefix='maipa-recovery-template-')
        cls.addClassCleanup(cls.template.cleanup)
        cls.origin = Path(cls.template.name).resolve() / 'install'
        result = subprocess.run([sys.executable, str(FIXTURE), 'seed'], cwd=ROOT,
            env=environment(cls.origin), capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError('Synthetic install fixture failed: ' + result.stderr)
        cls.proof = json.loads(result.stdout)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='maipa-recovery-test-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.data = self.base / 'install'
        shutil.copytree(self.origin, self.data)
        self.backup = self.base / 'snapshot'
        self.restored = self.base / 'restored'
        self.proof_file = self.base / 'synthetic-proof.json'
        self.proof_file.write_text(json.dumps(self.proof))

    def command(self, action, source, destination):
        return subprocess.run([sys.executable, '-m', 'backend.install', action,
            '--source', str(source), '--destination', str(destination)], cwd=ROOT,
            env=environment(self.data), capture_output=True, text=True, timeout=30)

    def snapshot(self):
        result = self.command('backup', self.data, self.backup)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'complete')
        return result

    def refused(self, result, destination):
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(destination.exists())
        for forbidden in ('synthetic-provider-secret-only', 'Synthetic saved profile', str(self.data)):
            self.assertNotIn(forbidden, result.stdout + result.stderr)
        self.assertEqual(list(self.base.glob('.maipa-stage-*')), [])

    def test_snapshot_restore_preserves_real_sessions_user_scope_and_private_files(self):
        result = self.snapshot()
        self.assertNotIn('synthetic-provider-secret-only', result.stdout + result.stderr)
        result = self.command('restore', self.backup, self.restored)
        self.assertEqual(result.returncode, 0, result.stderr)
        inspect = subprocess.run([sys.executable, str(FIXTURE), 'inspect', str(self.proof_file)],
            cwd=ROOT, env=environment(self.restored), capture_output=True, text=True, timeout=30)
        self.assertEqual(inspect.returncode, 0, inspect.stderr)
        state = json.loads(inspect.stdout)
        self.assertEqual(state['installation'], self.proof['installation'])
        self.assertEqual(state['layout_status'], 200)
        self.assertEqual(state['layout'], {'layout_id': self.proof['layout'], 'user_id': self.proof['user'],
            'revision': 4, 'split_basis_points': 3400})
        self.assertEqual(state['setup_status'], 200)
        self.assertEqual(state['setup']['setup_id'], self.proof['setup'])
        self.assertEqual(state['setup']['draft'], {'display_name': 'Synthetic saved profile'})
        self.assertFalse(state['setup']['provider_ready'])
        self.assertEqual(state['other_draft'], {})
        self.assertEqual(state['revocation'], [200, 403])
        self.assertEqual(state['revocation_cache'], 'no-store')
        self.assertEqual(state['users'], 2)
        self.assertFalse((self.restored / 'workers').exists())
        for folder in (self.backup, self.restored):
            for relative in ('development-key', 'local-setup-code',
                    'providers/11111111-1111-4111-8111-111111111111/synthetic.token',
                    'sessions/22222222-2222-4222-8222-222222222222/state.json',
                    'artifacts/33333333-3333-4333-8333-333333333333/proof.bin'):
                self.assertEqual((folder / relative).read_bytes(), (self.data / relative).read_bytes())
                if os.name == 'nt':
                    acl = descriptor(folder / relative)
                    self.assertIn('D:P', acl)
                    self.assertEqual(acl.count('(A;'), 2)
                else:
                    self.assertEqual((folder / relative).stat().st_mode & 0o777, 0o600)

    def test_committed_wal_records_are_in_the_actual_database_snapshot(self):
        connection = sqlite3.connect(self.data / 'maipa.sqlite3')
        self.addCleanup(connection.close)
        self.assertEqual(connection.execute('PRAGMA journal_mode=WAL').fetchone(), ('wal',))
        connection.execute('PRAGMA wal_autocheckpoint=0')
        connection.execute('CREATE TABLE synthetic_wal (value TEXT)')
        connection.execute('INSERT INTO synthetic_wal VALUES (?)', ('committed synthetic WAL data',))
        connection.commit()
        self.assertGreater((self.data / 'maipa.sqlite3-wal').stat().st_size, 0)
        self.snapshot()
        snapshot = sqlite3.connect(self.backup / 'maipa.sqlite3')
        self.addCleanup(snapshot.close)
        self.assertEqual(snapshot.execute('SELECT value FROM synthetic_wal').fetchall(), [('committed synthetic WAL data',)])

    def test_live_sqlite_writer_refuses_snapshot_and_preserves_source(self):
        writer = sqlite3.connect(self.data / 'maipa.sqlite3')
        self.addCleanup(writer.close)
        writer.execute('BEGIN IMMEDIATE')
        self.refused(self.command('backup', self.data, self.backup), self.backup)
        writer.rollback()
        self.snapshot()

    def test_real_install_lock_blocks_another_process_then_releases(self):
        holder = subprocess.Popen([sys.executable, str(FIXTURE), 'hold'], cwd=ROOT,
            env=environment(self.data), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        reader = ThreadPoolExecutor(max_workers=1)
        try:
            ready = reader.submit(holder.stdout.readline)
            self.assertEqual(ready.result(timeout=15).strip(), 'locked')
            self.refused(self.command('backup', self.data, self.backup), self.backup)
            output, error = holder.communicate('\n', timeout=15)
            self.assertEqual(holder.returncode, 0, error)
            self.assertTrue(json.loads(output)['released'])
        finally:
            if holder.poll() is None:
                holder.kill()
                holder.communicate(timeout=15)
            reader.shutdown(wait=True)
            for stream in (holder.stdin, holder.stdout, holder.stderr):
                stream.close()
        self.snapshot()

    def test_existing_destination_including_empty_directory_is_never_overwritten(self):
        self.snapshot()
        self.restored.mkdir()
        result = self.command('restore', self.backup, self.restored)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(self.restored.iterdir()), [])
        marker = self.restored / 'owned-synthetic-marker'
        marker.write_bytes(b'preserve this existing destination')
        result = self.command('restore', self.backup, self.restored)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(marker.read_bytes(), b'preserve this existing destination')

    def test_corrupt_snapshot_file_refuses_without_partial_restore(self):
        self.snapshot()
        path = self.backup / 'maipa.sqlite3'
        with path.open('r+b') as handle:
            handle.write(b'corrupted snapshot bytes')
        self.refused(self.command('restore', self.backup, self.restored), self.restored)

    def test_unmanifested_private_file_refuses_without_partial_restore(self):
        self.snapshot()
        (self.backup / 'unmanifested-secret').write_bytes(b'synthetic-only')
        self.refused(self.command('restore', self.backup, self.restored), self.restored)

    def test_hostile_manifest_path_version_and_duplicate_keys_refuse(self):
        self.snapshot()
        path = self.backup / 'snapshot.json'
        original = path.read_text()
        value = json.loads(original)
        modifications = []
        for name in ('../escape', 'C:/escape', 'providers/../../escape', 'artifacts/CON', 'artifacts/item:stream'):
            changed = json.loads(original)
            changed['files'][name] = next(iter(changed['files'].values()))
            modifications.append(json.dumps(changed))
        modifications += [json.dumps(value | {'version': 999}), original.replace('"version": 1', '"version": 1, "version": 1', 1)]
        for raw in modifications:
            with self.subTest(kind='hostile manifest'):
                path.write_text(raw)
                self.refused(self.command('restore', self.backup, self.restored), self.restored)
        self.assertFalse((self.base / 'escape').exists())

    def test_hardlinked_private_source_file_refuses_without_altering_target_acl(self):
        target = self.base / 'foreign-synthetic-token'
        target.write_bytes(b'synthetic-provider-secret-only')
        token = self.data / 'providers/11111111-1111-4111-8111-111111111111/synthetic.token'
        token.unlink()
        token.hardlink_to(target)
        before = descriptor(target) if os.name == 'nt' else target.stat().st_mode
        self.refused(self.command('backup', self.data, self.backup), self.backup)
        self.assertEqual(target.read_bytes(), b'synthetic-provider-secret-only')
        after = descriptor(target) if os.name == 'nt' else target.stat().st_mode
        self.assertEqual(after, before)

    def test_interrupted_copy_is_not_published_and_owned_staging_is_removed(self):
        from backend import install
        def interrupted(*_):
            raise OSError('Synthetic copy failure')
        with patch.object(install, 'copy_private', side_effect=interrupted), self.assertRaises(OSError):
            install.backup(self.data, self.backup)
        self.assertFalse(self.backup.exists())
        self.assertEqual(list(self.base.glob('.maipa-stage-*')), [])
        self.assertEqual((self.data / 'development-key').read_text(), 'synthetic-signing-key-' * 4)

    def test_unknown_durable_source_file_refuses_instead_of_omitting_it(self):
        (self.data / 'unrecognized-private-state').write_bytes(b'synthetic important private state')
        self.refused(self.command('backup', self.data, self.backup), self.backup)

    def legacy(self):
        code = self.base / 'legacy-code'
        (code / 'runtime').mkdir(parents=True)
        (code / 'frontend/dist').mkdir(parents=True)
        (code / 'frontend/dist/index.html').write_text('Synthetic legacy frontend')
        for name in ('maipa.sqlite3', 'development-key', 'local-setup-code'):
            shutil.copyfile(self.data / name, code / 'runtime' / name)
        return code

    def legacy_launch(self, code, data=None):
        from tests.test_install_storage import InstallLauncherTests
        # Reuse only the launcher fixture, without exporting another TestCase
        # class from this module or running any real server/admin bootstrap.
        harness = InstallLauncherTests('test_code_replacement_reuses_private_key_and_install_data')
        harness.code, harness.data = code, data or self.restored
        def forbidden(_):
            self.fail('Legacy adoption must retain the signing key')
        return harness.launch(generate=forbidden)

    def test_explicit_legacy_adoption_retains_records_keys_and_restarts_same_install(self):
        code = self.legacy()
        before = {name: (code / 'runtime' / name).read_bytes()
            for name in ('maipa.sqlite3', 'development-key', 'local-setup-code')}
        result = self.command('adopt-legacy', code, self.restored)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'complete')
        for name, raw in before.items():
            self.assertEqual((code / 'runtime' / name).read_bytes(), raw)
        for name in ('development-key', 'local-setup-code'):
            self.assertEqual((self.restored / name).read_bytes(), before[name])
        marker = code / 'runtime/install-location.json'
        binding = json.loads(marker.read_text())
        self.assertEqual(Path(binding['data_directory']), self.restored)
        self.assertEqual(binding['storage_id'], json.loads((self.restored / 'installation.json').read_text())['storage_id'])
        if os.name == 'nt':
            self.assertIn('D:P', descriptor(marker))
            self.assertEqual(descriptor(marker).count('(A;'), 2)
        calls, key = self.legacy_launch(code)
        self.assertEqual(key, 'synthetic-signing-key-' * 4)
        self.assertEqual(len(calls), 2)
        inspect = subprocess.run([sys.executable, str(FIXTURE), 'inspect', str(self.proof_file)],
            cwd=ROOT, env=environment(self.restored), capture_output=True, text=True, timeout=30)
        self.assertEqual(inspect.returncode, 0, inspect.stderr)
        self.assertEqual(json.loads(inspect.stdout)['installation'], self.proof['installation'])

    def test_legacy_writer_blocks_adoption_without_a_binding_or_partial_target(self):
        code = self.legacy()
        writer = sqlite3.connect(code / 'runtime/maipa.sqlite3')
        self.addCleanup(writer.close)
        writer.execute('BEGIN IMMEDIATE')
        self.refused(self.command('adopt-legacy', code, self.restored), self.restored)
        self.assertFalse((code / 'runtime/install-location.json').exists())
        writer.rollback()
        result = self.command('adopt-legacy', code, self.restored)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_legacy_replay_and_conflicting_destination_cannot_replace_install(self):
        code = self.legacy()
        first = self.command('adopt-legacy', code, self.restored)
        self.assertEqual(first.returncode, 0, first.stderr)
        binding = (code / 'runtime/install-location.json').read_bytes()
        key = (self.restored / 'development-key').read_bytes()
        replay = self.command('adopt-legacy', code, self.restored)
        self.assertNotEqual(replay.returncode, 0)
        second = self.base / 'second-install'
        self.refused(self.command('adopt-legacy', code, second), second)
        self.assertEqual((code / 'runtime/install-location.json').read_bytes(), binding)
        self.assertEqual((self.restored / 'development-key').read_bytes(), key)

    def test_legacy_startup_binding_requires_exact_directory_format_and_storage_identity(self):
        code = self.legacy()
        result = self.command('adopt-legacy', code, self.restored)
        self.assertEqual(result.returncode, 0, result.stderr)
        marker = code / 'runtime/install-location.json'
        original = json.loads(marker.read_text())
        for change in ({'data_directory': str(self.data)},
                {'storage_id': '99999999-9999-4999-8999-999999999999'}, {'version': 999},
                {'unexpected': 'field'}):
            with self.subTest(kind='invalid binding'):
                marker.write_text(json.dumps(original | change))
                with self.assertRaises(SystemExit):
                    self.legacy_launch(code)
        marker.unlink()
        with self.assertRaises(SystemExit):
            self.legacy_launch(code)

    def test_failed_legacy_binding_write_preserves_source_and_removes_unpublished_target(self):
        from backend import install
        code = self.legacy()
        keys = {name: (code / 'runtime' / name).read_bytes() for name in ('development-key', 'local-setup-code')}
        write = install.write_private
        def fail_binding(path, raw):
            if Path(path).name == 'install-location.json':
                raise PermissionError('Synthetic binding permission failure')
            return write(path, raw)
        with patch.object(install, 'write_private', side_effect=fail_binding), self.assertRaises(OSError):
            install.adopt_legacy(code, self.restored)
        self.assertFalse(self.restored.exists())
        self.assertFalse((code / 'runtime/install-location.json').exists())
        self.assertEqual(list(self.base.glob('.maipa-stage-*')), [])
        for name, raw in keys.items():
            self.assertEqual((code / 'runtime' / name).read_bytes(), raw)

    def test_legacy_sqlite_sidecar_hardlink_is_rejected_before_sqlite_opens_any_file(self):
        from backend import install
        code = self.legacy()
        target = self.base / 'foreign-synthetic-wal'
        target.write_bytes(b'foreign synthetic WAL bytes')
        sidecar = code / 'runtime/maipa.sqlite3-wal'
        sidecar.hardlink_to(target)
        before = descriptor(target) if os.name == 'nt' else target.stat().st_mode
        with patch.object(install.sqlite3, 'connect') as opening, self.assertRaises(OSError):
            install.adopt_legacy(code, self.restored)
        opening.assert_not_called()
        self.assertEqual(target.read_bytes(), b'foreign synthetic WAL bytes')
        self.assertTrue(sidecar.exists())
        self.assertEqual(target.stat().st_nlink, 2)
        after = descriptor(target) if os.name == 'nt' else target.stat().st_mode
        self.assertEqual(after, before)
        self.assertFalse(self.restored.exists())

    def alter_snapshot(self, name, raw):
        import hashlib
        path = self.backup / name
        path.write_bytes(raw)
        manifest = self.backup / 'snapshot.json'
        value = json.loads(manifest.read_text())
        value['files'][name] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        manifest.write_text(json.dumps(value))

    def test_matching_file_hash_does_not_make_an_invalid_sqlite_database_restorable(self):
        self.snapshot()
        self.alter_snapshot('maipa.sqlite3', b'not a real SQLite database')
        self.refused(self.command('restore', self.backup, self.restored), self.restored)

    def test_matching_file_hash_does_not_make_a_malformed_signing_key_restorable(self):
        self.snapshot()
        self.alter_snapshot('development-key', b'synthetic-short-key')
        self.refused(self.command('restore', self.backup, self.restored), self.restored)

    def test_unrecognized_source_does_not_create_or_reprotect_a_foreign_lockfile(self):
        foreign = self.base / 'unrelated-synthetic-folder'
        foreign.mkdir()
        lock = foreign / 'maintenance.lock'
        lock.write_bytes(b'unrelated synthetic lockfile bytes')
        before = descriptor(lock) if os.name == 'nt' else lock.stat().st_mode
        self.refused(self.command('backup', foreign, self.backup), self.backup)
        self.assertEqual(lock.read_bytes(), b'unrelated synthetic lockfile bytes')
        after = descriptor(lock) if os.name == 'nt' else lock.stat().st_mode
        self.assertEqual(after, before)
        lock.unlink()
        self.refused(self.command('backup', foreign, self.backup), self.backup)
        self.assertEqual(list(foreign.iterdir()), [])

    def test_backup_refuses_a_manifest_larger_than_its_restore_read_budget(self):
        from backend import install
        # Scale only the format's capacity, not copy/integrity/publication I/O.
        # A successful snapshot must always be readable under the same budget.
        with patch.object(install, 'MANIFEST_BUDGET', 64, create=True), self.assertRaises(OSError):
            install.backup(self.data, self.backup)
        self.assertFalse(self.backup.exists())
        self.assertEqual(list(self.base.glob('.maipa-stage-*')), [])
        self.assertEqual((self.data / 'development-key').read_text(), 'synthetic-signing-key-' * 4)

    def test_seed_fixture_requires_explicit_marker_and_owned_synthetic_path(self):
        with tempfile.TemporaryDirectory(prefix='maipa-recovery-test-') as temporary:
            unmarked = Path(temporary).resolve() / 'install'
            wrong_path = self.base / 'unrelated-fixture-target'
            for path, marked in ((unmarked, False), (wrong_path, True)):
                with self.subTest(kind='unmarked' if not marked else 'wrong path'):
                    self.assertFalse(path.exists())
                    env = environment(path)
                    if marked:
                        env['MAIPA_STORAGE_FIXTURE'] = '1'
                    else:
                        env.pop('MAIPA_STORAGE_FIXTURE', None)
                    result = subprocess.run([sys.executable, str(FIXTURE), 'seed'],
                        cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(path.exists())
