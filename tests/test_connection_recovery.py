"""Restart acceptance under real installation/profile locks; no machine credentials."""
from datetime import timedelta
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from backend.maipa.agent_accounts import chat, profiles
from backend.maipa.agent_accounts.models import AgentAccount, Conversation, ProviderProfile, SessionReference
from backend.maipa.agent_accounts.references import record_session
from backend.maipa.agent_accounts.ui import open_ui_lease, dispatch_ui
from backend.maipa.storage import StorageUnavailable, install_lock, prepare_directory, prepare_files
from backend.maipa.windows_sandbox import ProcessWitness, remove_abandoned_profile
from backend.maipa.workspace.models import Installation, Membership


class ConnectionRecoveryTests(TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='maipa-restart-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.data = prepare_directory(self.root/'install')
        prepare_files(self.data)
        configured = override_settings(DATA_ROOT=self.data)
        configured.enable(); self.addCleanup(configured.disable)
        self.installation = Installation.objects.create()
        self.owner = get_user_model().objects.create_user(username='restart-owner')
        Membership.objects.create(user=self.owner, installation=self.installation, role='preparer')
        self.account = AgentAccount.objects.create(owner=self.owner, provider='claude', label='Existing selected account')
        self.profile = ProviderProfile.objects.get(pk=profiles.provision_profile(self.owner, str(self.account.pk)))
        self.base = self.data/'providers'/self.profile.id.hex
        self.home = self.base/'home'
        self.private = self.home/'synthetic-provider-state'
        self.private.write_bytes(b'SYNTHETIC retained subscription state')
        self.user_message = {'id': str(uuid.uuid4()), 'role': 'user', 'text': 'Keep this queued request', 'state': 'queued'}
        self.partial = {'id': str(uuid.uuid4()), 'role': 'assistant', 'text': 'Keep this partial reply', 'state': 'streaming'}
        self.row = Conversation.objects.create(user=self.owner, account=self.account,
            installation=self.installation, storage_id=self.profile.storage_id,
            phase='sign_in', worker=uuid.uuid4(), heartbeat=timezone.now()-timedelta(minutes=40),
            revision=7, messages=[self.user_message, self.partial], requests=[self.user_message['id']])

    def recover(self, guard):
        from backend.maipa.agent_accounts.recovery import recover_connections
        return recover_connections(guard)

    def orphan(self):
        runtime=self.base/'worker-abc12345'
        for name in ('code','supervisor','workspace'):(runtime/name).mkdir(parents=True)
        (runtime/'code/node.exe').write_bytes(b'Synthetic public runtime, not executable')
        (runtime/'workspace/retained-work').write_bytes(b'Synthetic temporary work retained for recovery')
        return runtime

    def test_restart_retires_signin_and_old_ui_without_losing_original_account_or_messages(self):
        self.assertEqual(chat.snapshot(self.owner)['phase'],'sign_in')
        runtime=self.orphan()
        session=record_session(self.owner, str(self.account.pk), 'synthetic-old-native-session')
        ui=open_ui_lease(self.owner, str(self.account.pk), session)
        composition={'title':'Retained commentary','schema':{'id':'saved-note','type':'Maipa.Note','props':{'text':'Keep this commentary'}}}
        dispatch_ui(self.owner,str(self.account.pk),ui,{'method':'tools/call','params':{
            'name':'maipa_ui_present','arguments':{'revision':0,**composition}}})
        Conversation.objects.filter(pk=self.row.pk).update(ui_id=ui)
        original_open=Path.open
        def no_credentials(path,*args,**kwargs):
            if path==self.private:raise AssertionError('Recovery must not read provider state')
            return original_open(path,*args,**kwargs)
        with install_lock(self.data) as guard, patch.object(Path,'open',no_credentials):
            result=self.recover(guard)
        self.assertEqual(result,{'conversations':1,'quarantined_runtimes':1})
        self.row.refresh_from_db();self.profile.refresh_from_db();self.account.refresh_from_db()
        self.assertEqual((self.row.user_id,self.row.account_id,self.row.storage_id),
                         (self.owner.pk,self.account.pk,self.profile.storage_id))
        self.assertEqual(self.row.messages,[self.user_message,{**self.partial,'state':'interrupted'}])
        self.assertEqual(self.row.requests,[self.user_message['id']])
        self.assertEqual((self.row.phase,self.row.worker,self.row.heartbeat,self.row.ui_id,self.row.revision),
                         ('offline',None,None,None,8))
        self.assertIsNotNone(SessionReference.objects.get(pk=session).revoked_at)
        self.assertEqual(chat.snapshot(self.owner)['phase'],'offline')
        self.assertEqual(self.account.provider,'claude')
        self.assertEqual(self.private.read_bytes(),b'SYNTHETIC retained subscription state')
        self.assertFalse(runtime.exists())
        quarantined=list((self.data/'artifacts/runtime-recovery').iterdir())
        self.assertEqual(len(quarantined),1)
        self.assertEqual((quarantined[0]/'workspace/retained-work').read_bytes(),b'Synthetic temporary work retained for recovery')
        self.assertEqual(set(p.name for p in self.base.iterdir()),{'home','binding.json','maintenance.lock'})

    def test_repeated_recovery_keeps_revision_and_quarantined_payload_unchanged(self):
        self.orphan()
        with install_lock(self.data) as guard:
            self.recover(guard)
            self.row.refresh_from_db();revision=self.row.revision
            self.assertEqual(self.recover(guard),{'conversations':0,'quarantined_runtimes':0})
        self.row.refresh_from_db()
        self.assertEqual(self.row.revision,revision)
        self.assertEqual(len(list((self.data/'artifacts/runtime-recovery').iterdir())),1)

    def test_missing_closed_or_other_installation_guard_cannot_recover(self):
        with self.assertRaises(StorageUnavailable):self.recover(None)
        with install_lock(self.data) as closed:pass
        with self.assertRaises(StorageUnavailable):self.recover(closed)
        other=prepare_directory(self.root/'other-install')
        with install_lock(other) as wrong:
            with self.assertRaises(StorageUnavailable):self.recover(wrong)
        self.row.refresh_from_db()
        self.assertEqual((self.row.phase,self.row.revision),('sign_in',7))

    def test_active_native_profile_lease_prevents_restart_recovery(self):
        with profiles.lease_profile(self.owner,str(self.account.pk),str(self.profile.pk)):
            with install_lock(self.data) as guard:
                with self.assertRaises(StorageUnavailable):self.recover(guard)
        self.row.refresh_from_db()
        self.assertEqual((self.row.phase,self.row.revision),('sign_in',7))
        self.assertFalse((self.data/'artifacts/runtime-recovery').exists())

    def test_aliased_runtime_payload_is_refused_before_quarantine_or_state_change(self):
        runtime=self.orphan()
        foreign=self.root/'foreign-file';foreign.write_bytes(b'SYNTHETIC unrelated bytes')
        linked=runtime/'workspace/alias'
        os.link(foreign,linked)
        try:
            with install_lock(self.data) as guard:
                with self.assertRaises(StorageUnavailable):self.recover(guard)
            self.row.refresh_from_db()
            self.assertEqual((self.row.phase,self.row.revision),('sign_in',7))
            self.assertTrue(runtime.is_dir())
            self.assertEqual(foreign.read_bytes(),b'SYNTHETIC unrelated bytes')
        finally:linked.unlink()

    def test_foreign_binding_or_unknown_runtime_layout_is_not_adopted(self):
        runtime=self.orphan()
        unknown=runtime/'unknown-root-entry';unknown.write_bytes(b'SYNTHETIC unknown layout')
        with install_lock(self.data) as guard:
            with self.assertRaises(StorageUnavailable):self.recover(guard)
        unknown.unlink()
        ProviderProfile.objects.filter(pk=self.profile.pk).update(storage_id=uuid.uuid4())
        with install_lock(self.data) as guard:
            with self.assertRaises(StorageUnavailable):self.recover(guard)
        self.row.refresh_from_db()
        self.assertEqual((self.row.phase,self.row.revision),('sign_in',7))
        self.assertTrue(runtime.is_dir())
        self.assertEqual(self.private.read_bytes(),b'SYNTHETIC retained subscription state')

    def test_live_owned_runtime_is_not_touched_even_when_the_old_locks_are_free(self):
        runtime=self.orphan()
        node=runtime/'code/node.exe'
        shutil.copyfile(Path(shutil.which('node')).resolve(strict=True),node)
        environment={k:v for k,v in os.environ.items() if k.upper() in ('PATH','SYSTEMROOT','WINDIR','TEMP','TMP')}
        process=subprocess.Popen([str(node),'-e',"process.stdout.write('ready\\n'); setTimeout(()=>process.exit(0),15000)"],
            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8',cwd=self.root,env=environment)
        try:
            self.assertEqual(process.stdout.readline(),'ready\n')
            with ProcessWitness(process.pid) as witness:
                self.assertFalse(witness.exited)
                with install_lock(self.data) as guard, patch('backend.maipa.agent_accounts.recovery.os.rename',
                        side_effect=AssertionError('A live owned runtime must never reach quarantine')):
                    with self.assertRaises(StorageUnavailable):self.recover(guard)
                self.assertFalse(witness.exited)
            self.row.refresh_from_db()
            self.assertEqual((self.row.phase,self.row.revision),('sign_in',7))
            self.assertTrue(runtime.is_dir())
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=5)
            process.stdout.close()

    def test_real_crashed_server_releases_locks_and_kernel_stops_descendants_before_recovery(self):
        node=shutil.which('node');self.assertIsNotNone(node)
        runtime=self.base/'worker-crash123'
        fixture=Path(__file__).parent/'fixtures/recovery_owner.py'
        prepared=self.root/'crash-environment-ready'
        process=subprocess.Popen([sys.executable,str(fixture),str(self.data),str(self.base),str(runtime),node,str(prepared)],
            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
        profile_name=None
        try:
            # Actual lock/copy/sandbox setup precedes the original ten-second
            # process-tree observation. No PID, readiness or recovery is faked.
            deadline=time.monotonic()+15
            while not prepared.is_file():
                self.assertIsNone(process.poll(),'Finite crash fixture exited during setup')
                self.assertLess(time.monotonic(),deadline,'Finite crash fixture setup unavailable')
                time.sleep(.01)
            bootstrap=json.loads(prepared.read_text(encoding='utf-8'));self.assertEqual(set(bootstrap),{'owner_pid','profile_name'})
            profile_name=bootstrap['profile_name']
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                observed=json.loads(pool.submit(process.stdout.readline).result(timeout=10))
            profile_name=observed['profile_name']
            self.assertEqual(observed['owner_pid'],bootstrap['owner_pid'])
            self.assertEqual(observed['profile_name'],bootstrap['profile_name'])
            with ProcessWitness(observed['owner_pid']) as owner, ProcessWitness(observed['root_pid']) as root, ProcessWitness(observed['descendant_pid']) as descendant:
                self.assertFalse(owner.exited);self.assertFalse(root.exited);self.assertFalse(descendant.exited)
                process.kill();process.wait(timeout=5)
                # A Windows venv redirector can exit before its real Python
                # interpreter releases the installation/profile locks. Require
                # that exact server's exit, rather than treating launcher exit
                # or a recycled PID as proof that offline maintenance is safe.
                self.assertTrue(owner.wait(5),'The actual lock-owning server must exit before recovery')
                with install_lock(self.data) as guard:
                    self.assertEqual(self.recover(guard),{'conversations':1,'quarantined_runtimes':1})
                self.assertTrue(root.exited);self.assertTrue(descendant.exited)
            from tests.test_install_storage import descriptor
            quarantine=next((self.data/'artifacts/runtime-recovery').iterdir())
            for path in (self.base,self.home,self.private,quarantine,quarantine/'code/node.exe',quarantine/'workspace'):
                acl=descriptor(path)
                self.assertIn('D:P',acl)
                self.assertEqual(acl.count('(A;'),2,'A retired container must not retain private file access')
                self.assertNotIn('S-1-15-2-',acl)
            self.row.refresh_from_db()
            self.assertEqual(self.row.phase,'offline')
            self.assertIsNone(self.row.worker)
            self.assertFalse(runtime.exists())
            self.assertEqual(self.private.read_bytes(),b'SYNTHETIC retained subscription state')
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=5)
            process.stdout.close()
            if profile_name:self.assertTrue(remove_abandoned_profile(profile_name))
