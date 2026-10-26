"""Independent P06 original-profile ownership and actual OS lease outcomes."""
import json
import asyncio
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import uuid

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from backend.maipa.agent_accounts import profiles
from backend.maipa.agent_accounts.models import AgentAccount
from backend.maipa.acp import AcpClient, Scope
from backend.maipa.worker_capsule import WorkerCapsule
from backend.maipa.windows_sandbox import WindowsSandbox, SandboxUnavailable
from backend.maipa.storage import prepare_directory, prepare_files
from backend.maipa.workspace.models import Installation, Membership
from tests.test_install_storage import descriptor


class ProviderProfileTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='maipa-profile-test-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.data = prepare_directory(self.base / 'install')
        prepare_files(self.data)
        configured = override_settings(DATA_ROOT=self.data)
        configured.enable()
        self.addCleanup(configured.disable)
        self.installation = Installation.objects.create()
        self.owner = get_user_model().objects.create_user(username='profile-owner')
        self.other = get_user_model().objects.create_user(username='profile-other')
        self.member = Membership.objects.create(installation=self.installation, user=self.owner, role='preparer')
        Membership.objects.create(installation=self.installation, user=self.other, role='preparer')
        self.account = AgentAccount.objects.create(owner=self.owner, provider='codex', label='Synthetic profile account')
        self.foreign = AgentAccount.objects.create(owner=self.other, provider='codex', label='Synthetic other account')

    def provision(self, actor=None, account=None):
        return profiles.provision_profile(actor or self.owner, str((account or self.account).pk))

    def lease(self, reference, actor=None, account=None):
        return profiles.lease_profile(actor or self.owner, str((account or self.account).pk), reference)

    def refused(self, operation):
        with self.assertRaises(profiles.ProfileUnavailable) as caught:
            operation()
        self.assertEqual(str(caught.exception), 'Agent profile unavailable')
        self.assertIsNone(caught.exception.__cause__)

    def open_once(self, reference, actor=None, account=None):
        with self.lease(reference, actor, account) as lease:
            return lease.home

    def child_lock(self, directory, mode='try'):
        return [sys.executable, str(Path(__file__).parent / 'fixtures/profile_lock.py'), str(directory), mode]

    def test_original_private_home_survives_new_lease_and_provision_without_readiness_claim(self):
        reference = self.provision()
        self.assertEqual(str(uuid.UUID(reference)), reference)
        self.assertNotEqual(reference, str(self.account.pk))
        with self.lease(reference) as first:
            home = first.home
            self.assertTrue(home.is_absolute())
            self.assertTrue(home.is_relative_to(self.data))
            self.assertEqual(list(home.iterdir()), [])
            (home / 'synthetic-state').write_bytes(b'owned-state')
            self.assertNotIn(str(home), repr(first))
            self.assertFalse(hasattr(first, 'provider_ready'))
            self.assertEqual(first.scope.user_id, str(self.owner.pk))
            self.assertEqual(first.scope.account_id, str(self.account.pk))
            self.assertEqual(first.provider, 'codex')
            first.revalidate()
        self.assertEqual(self.provision(), reference)
        with self.lease(reference) as second:
            self.assertEqual(second.home, home)
            self.assertEqual((second.home / 'synthetic-state').read_bytes(), b'owned-state')

    def test_foreign_and_malformed_references_never_open_or_allocate_another_home(self):
        reference = self.provision()
        before = set((self.data / 'providers').iterdir())
        for value in ('../private', str(uuid.uuid4()), reference.upper(), None):
            self.refused(lambda: self.open_once(value))
        self.refused(lambda: self.open_once(reference, self.other, self.foreign))
        self.refused(lambda: self.provision(self.other, self.account))
        self.assertEqual(set((self.data / 'providers').iterdir()), before)

    def test_reassigned_owner_or_provider_cannot_adopt_the_original_profile(self):
        reference = self.provision()
        home = self.open_once(reference)
        (home / 'synthetic-state').write_bytes(b'original-state')
        self.foreign.delete()
        AgentAccount.objects.filter(pk=self.account.pk).update(owner=self.other)
        self.refused(lambda: self.open_once(reference, self.other, self.account))
        self.refused(lambda: self.provision(self.other, self.account))
        AgentAccount.objects.filter(pk=self.account.pk).update(owner=self.owner, provider='claude')
        self.refused(lambda: self.provision())
        self.refused(lambda: self.open_once(reference))
        self.assertEqual((home / 'synthetic-state').read_bytes(), b'original-state')

    def test_current_disabled_user_membership_account_and_restricted_roles_refuse_every_entry(self):
        reference = self.provision()
        changes = [(get_user_model().objects.filter(pk=self.owner.pk), {'is_active': False}, {'is_active': True}),
                   (Membership.objects.filter(pk=self.member.pk), {'enabled': False}, {'enabled': True}),
                   (Membership.objects.filter(pk=self.member.pk), {'role': 'viewer'}, {'role': 'preparer'}),
                   (AgentAccount.objects.filter(pk=self.account.pk), {'enabled': False}, {'enabled': True})]
        for query, revoked, restored in changes:
            query.update(**revoked)
            self.refused(lambda: self.provision())
            self.refused(lambda: self.open_once(reference))
            query.update(**restored)
        self.assertTrue(self.open_once(reference).is_dir())

    def test_storage_identity_change_never_rebinds_private_state(self):
        reference = self.provision()
        home = self.open_once(reference)
        marker = self.data / 'installation.json'
        original = marker.read_bytes()
        value = json.loads(original)
        value['storage_id'] = str(uuid.uuid4())
        marker.write_text(json.dumps(value))
        self.refused(lambda: self.provision())
        self.refused(lambda: self.open_once(reference))
        marker.write_bytes(original)
        self.assertEqual(self.open_once(reference), home)

    def test_tampered_binding_marker_is_refused_without_repair_or_payload_mutation(self):
        reference = self.provision()
        home = self.open_once(reference)
        (home / 'synthetic-state').write_bytes(b'unchanged')
        marker = home.parent / 'binding.json'
        marker.write_text('{"version":1,"version":1}')
        self.refused(lambda: self.open_once(reference))
        self.refused(lambda: self.provision())
        self.assertEqual(marker.read_text(), '{"version":1,"version":1}')
        self.assertEqual((home / 'synthetic-state').read_bytes(), b'unchanged')

    def test_competing_real_process_is_refused_then_acquires_after_lease_close(self):
        reference = self.provision()
        with self.lease(reference) as lease:
            directory = lease.home.parent
            busy = subprocess.run(self.child_lock(directory), capture_output=True, text=True, timeout=10)
            self.assertEqual((busy.returncode, busy.stdout.strip()), (23, 'busy'))
            self.refused(lambda: self.open_once(reference))
        free = subprocess.run(self.child_lock(directory), capture_output=True, text=True, timeout=10)
        self.assertEqual((free.returncode, free.stdout.strip()), (0, 'acquired'))
        self.assertEqual(self.open_once(reference).parent, directory)

    def test_actual_lock_owner_death_releases_lease_without_deleting_home(self):
        reference = self.provision()
        home = self.open_once(reference)
        (home / 'synthetic-state').write_bytes(b'retained-after-death')
        child = subprocess.Popen(self.child_lock(home.parent, 'hold'), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'acquired')
            self.refused(lambda: self.open_once(reference))
            child.kill()
            child.wait(timeout=10)
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=10)
        self.assertEqual(self.open_once(reference), home)
        self.assertEqual((home / 'synthetic-state').read_bytes(), b'retained-after-death')

    def test_revoked_open_lease_cannot_revalidate_and_closed_lease_stays_unusable(self):
        reference = self.provision()
        with self.lease(reference) as lease:
            Membership.objects.filter(pk=self.member.pk).update(enabled=False)
            self.refused(lease.revalidate)
            Membership.objects.filter(pk=self.member.pk).update(enabled=True)
            lease.revalidate()
        self.refused(lease.revalidate)
        self.assertTrue(self.open_once(reference).is_dir())

    def test_consumer_exception_releases_lock_and_preserves_durable_home(self):
        reference = self.provision()
        with self.assertRaisesRegex(RuntimeError, '^synthetic consumer failure$'):
            with self.lease(reference) as lease:
                (lease.home / 'synthetic-state').write_bytes(b'durable')
                raise RuntimeError('synthetic consumer failure')
        self.assertEqual((self.open_once(reference) / 'synthetic-state').read_bytes(), b'durable')

    def test_hardlinked_lock_is_refused_without_changing_foreign_file(self):
        reference = self.provision()
        home = self.open_once(reference)
        target = self.base / 'foreign-lock'
        target.write_bytes(b'foreign-state')
        lock = home.parent / 'maintenance.lock'
        lock.unlink()
        os.link(target, lock)
        try:
            self.refused(lambda: self.open_once(reference))
            self.assertEqual(target.read_bytes(), b'foreign-state')
            self.assertEqual(target.stat().st_nlink, 2)
        finally:
            lock.unlink()

    def test_linked_home_is_refused_before_grant_or_foreign_tree_mutation(self):
        reference = self.provision()
        home = self.open_once(reference)
        home.rmdir()
        target = self.base / 'foreign-home'
        target.mkdir()
        (target / 'synthetic-state').write_bytes(b'foreign-state')
        tool = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/cmd.exe'
        subprocess.run([str(tool), '/d', '/c', 'mklink', '/J', str(home), str(target)],
                       capture_output=True, check=True, timeout=10)
        try:
            self.refused(lambda: self.open_once(reference))
            self.refused(lambda: self.provision())
            self.assertEqual((target / 'synthetic-state').read_bytes(), b'foreign-state')
            self.assertEqual(set(target.iterdir()), {target / 'synthetic-state'})
        finally:
            home.rmdir()

    def test_private_profile_is_unavailable_through_public_routes(self):
        reference = self.provision()
        home = self.open_once(reference)
        payload = b'synthetic-private-provider-state'
        (home / 'synthetic-state').write_bytes(payload)
        client = Client()
        client.force_login(self.owner)
        for url in (f'/api/agent/profiles/{reference}/', f'/providers/{uuid.UUID(reference).hex}/home/synthetic-state'):
            response = client.get(url, secure=True)
            self.assertEqual(response.status_code, 404)
            self.assertNotIn(payload, response.content)
            self.assertNotIn(str(home).encode(), response.content)

    def test_real_isolated_capsule_uses_leased_home_and_refuses_another_account_home(self):
        reference = self.provision()
        foreign_reference = self.provision(self.other, self.foreign)
        with self.lease(foreign_reference, self.other, self.foreign) as foreign:
            foreign_home = foreign.home
            (foreign_home / 'synthetic-state').write_text('foreign-state')
        with self.lease(reference) as lease:
            home = lease.home
            (home / 'synthetic-state').write_text('owned-state')
            runtime = Path(__file__).resolve().parents[1] / 'backend/agent'
            with WorkerCapsule(home.parent, runtime, shutil.which('node'), lease.scope,
                               lease.provider, profile_lease=lease) as capsule:
                self.assertEqual(capsule.home, home)
                self.assertEqual(capsule.profile['home'], str(home))
                self.assertEqual(capsule.isolation_root, home.parent)
                base = capsule.base
                script = capsule.code / 'profile-probe.mjs'
                script.write_text("import {readFileSync,writeFileSync} from 'node:fs';"
                    "const read=p=>{try{return readFileSync(p,'utf8')}catch(e){return e.code}};"
                    "writeFileSync(process.argv[2]+'/worker-state','retained-worker-state');"
                    "console.log(JSON.stringify([read(process.argv[2]+'/synthetic-state'),"
                    "read(process.argv[3]+'/synthetic-state')]));")
                async def run():
                    with WindowsSandbox(lease.scope, capsule.isolation_root,
                            writable=(capsule.workspace, capsule.home), readonly=(capsule.code, capsule.supervisor),
                            installation_id=str(self.installation.pk)) as sandbox:
                        command = [*capsule.command[:-1], str(script), str(home), str(foreign_home)]
                        child = await sandbox.spawn(command, scope=lease.scope, cwd=capsule.workspace, env={}, limit=4096)
                        result = json.loads(await asyncio.wait_for(child.stdout.readline(), 10))
                        self.assertEqual(result[0], 'owned-state')
                        self.assertIn(result[1], ('EACCES', 'EPERM'))
                        self.assertEqual(await child.wait(), 0)
                        self.assertTrue(child.job_empty)
                    self.assertTrue(sandbox.profile_removed)
                asyncio.run(run())
            self.assertFalse(base.exists())
            self.assertEqual((home / 'worker-state').read_text(), 'retained-worker-state')
        self.assertEqual((self.open_once(reference) / 'worker-state').read_text(), 'retained-worker-state')
        self.assertEqual((foreign_home / 'synthetic-state').read_text(), 'foreign-state')

    def test_capsule_requires_original_lease_scope_provider_root_and_open_lifetime(self):
        reference = self.provision()
        runtime = Path(__file__).resolve().parents[1] / 'backend/agent'
        with self.lease(reference) as lease:
            for root, scope, provider in ((lease.home.parent, Scope('foreign', lease.scope.account_id), 'codex'),
                    (lease.home.parent, lease.scope, 'claude'), (self.base, lease.scope, 'codex')):
                with self.assertRaises(SandboxUnavailable):
                    with WorkerCapsule(root, runtime, shutil.which('node'), scope, provider, profile_lease=lease):
                        self.fail('A foreign profile lease was consumed')
        with self.assertRaises(SandboxUnavailable):
            with WorkerCapsule(lease.home.parent, runtime, shutil.which('node'), lease.scope,
                               'codex', profile_lease=lease):
                self.fail('A closed profile lease was consumed')
        self.assertEqual(list(lease.home.iterdir()), [])

    def test_published_host_initializes_in_original_profile_without_claiming_provider_login(self):
        reference = self.provision()
        runtime = Path(__file__).resolve().parents[1] / 'backend/agent'
        with self.lease(reference) as lease:
            with WorkerCapsule(lease.home.parent, runtime, shutil.which('node'), lease.scope,
                               lease.provider, profile_lease=lease) as capsule:
                async def run():
                    with WindowsSandbox(lease.scope, capsule.isolation_root,
                            writable=(capsule.workspace, capsule.home), readonly=(capsule.code, capsule.supervisor),
                            installation_id=str(self.installation.pk)) as sandbox:
                        client = AcpClient(capsule.command, lease.scope, capsule.workspace, launcher=sandbox,
                            env={'MAIPA_ACP_PROFILE': json.dumps(capsule.profile)}, timeout=10)
                        await client.start()
                        try:
                            initialized = await client.initialize(lease.scope)
                            self.assertEqual(initialized['agentInfo']['name'], 'maipa-prism-acp')
                            self.assertEqual(initialized['authMethods'], [])
                            self.assertEqual(await client.shutdown(lease.scope),
                                             {'verified': True, 'survivors': 0, 'sessions': 0})
                        finally:
                            await client.close()
                        self.assertEqual(client.returncode, 0)
                        self.assertTrue(sandbox.process.job_empty)
                    self.assertTrue(sandbox.profile_removed)
                asyncio.run(run())
                lease.revalidate()

    def test_lease_close_removes_temporary_worker_grants_from_retained_profile(self):
        reference = self.provision()
        with self.lease(reference) as lease:
            home = lease.home
            payload = home / 'synthetic-state'
            payload.write_bytes(b'retained-private-state')
            with WindowsSandbox(lease.scope, home.parent, writable=(home,), readonly=(),
                                installation_id=str(self.installation.pk)) as sandbox:
                sid = sandbox._sid_string
                self.assertIn(sid, descriptor(home))
                self.assertIn(sid, descriptor(payload))
            self.assertTrue(sandbox.profile_removed)
        for path in (home.parent, home, payload):
            self.assertNotIn(sid, descriptor(path))
            self.assertNotIn(';;;WD)', descriptor(path))
            self.assertNotIn(';;;BU)', descriptor(path))
        self.assertEqual(payload.read_bytes(), b'retained-private-state')
        self.assertEqual(self.open_once(reference), home)

    def test_overbudget_profile_is_refused_without_reading_or_deleting_its_payload(self):
        reference = self.provision()
        home = self.open_once(reference)
        payload = home / 'synthetic-sparse-state'
        with payload.open('wb') as output:
            output.truncate(536_870_913)
        self.refused(lambda: self.open_once(reference))
        self.refused(lambda: self.provision())
        self.assertEqual(payload.stat().st_size, 536_870_913)
        payload.unlink()
        self.assertEqual(self.open_once(reference), home)
