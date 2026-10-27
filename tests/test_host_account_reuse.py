"""Host-user execution reuses CLI-owned auth; no credential files are read."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from backend.maipa.acp import Scope
from backend.maipa.windows_sandbox import WindowsHostWorker, ProcessWitness


class HostAccountReuseTests(unittest.TestCase):
    def test_owner_death_ends_host_worker_and_descendant_without_credential_access(self):
        with tempfile.TemporaryDirectory(prefix='maipa-host-crash-') as temporary:
            root=Path(temporary).resolve()
            (root/'finite.mjs').write_text('''const {spawn}=await import('node:child_process');
const child=spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'ignore',windowsHide:true});
console.log(child.pid);setInterval(()=>{},1000);''',encoding='utf-8')
            fixture=Path(__file__).parent/'fixtures/host_owner.py'
            parent=subprocess.Popen([sys.executable,str(fixture),str(root)],stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    line=executor.submit(parent.stdout.readline).result(timeout=12)
                worker,descendant=map(int,line.split())
                with ProcessWitness(worker) as first, ProcessWitness(descendant) as second:
                    self.assertFalse(first.exited);self.assertFalse(second.exited)
                    parent.kill();parent.wait(timeout=5)
                    self.assertTrue(first.wait(5));self.assertTrue(second.wait(5))
            finally:
                if parent.poll() is None:parent.kill();parent.wait(timeout=5)
                parent.stdout.close()

    def test_host_runs_as_local_user_with_closed_environment_and_kills_descendants(self):
        async def run():
            with tempfile.TemporaryDirectory(prefix='maipa-host-reuse-') as temporary:
                root=Path(temporary).resolve();workspace=root/'workspace';workspace.mkdir()
                scope=Scope('host-owner','host-account')
                script=root/'finite.mjs'
                script.write_text('''import {spawn} from 'node:child_process';
const child=spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'ignore',windowsHide:true});
console.log(JSON.stringify({child:child.pid,home:!!process.env.USERPROFILE,appdata:!!process.env.APPDATA,
secret:!!process.env.ANTHROPIC_API_KEY,inject:!!process.env.NODE_OPTIONS,profile:process.env.MAIPA_ACP_PROFILE}));
process.stdin.resume();setInterval(()=>{},1000);''',encoding='utf-8')
                with WindowsHostWorker(scope,workspace) as launcher:
                    with self.assertRaises(PermissionError):
                        await launcher.spawn((shutil.which('node'),str(script)),scope=Scope('other','host-account'),cwd=workspace,env={},limit=65536)
                    process=await launcher.spawn((shutil.which('node'),str(script)),scope=scope,cwd=workspace,
                        env={'ANTHROPIC_API_KEY':'synthetic-refused','NODE_OPTIONS':'synthetic-refused','MAIPA_ACP_PROFILE':'synthetic-owner'},limit=65536)
                    result=json.loads(await asyncio.wait_for(process.stdout.readline(),10))
                    self.assertEqual(result,{'child':result['child'],'home':True,'appdata':True,'secret':False,'inject':False,'profile':'synthetic-owner'})
                    self.assertEqual(process.isolation,'windows-host-user')
                    self.assertTrue(launcher.host_token_verified)
                    with ProcessWitness(result['child']) as witness:
                        # Job termination is asynchronous for each member.
                        # Verify this immutable descendant handle as well as
                        # root/job completion, within the SAME ten-second stop
                        # budget, before removing its working directory.
                        stop_deadline = time.monotonic() + 10
                        process.kill();await asyncio.wait_for(process.wait(),max(.001,stop_deadline-time.monotonic()))
                        self.assertTrue(witness.wait(max(0,stop_deadline-time.monotonic())))
                        self.assertTrue(witness.exited)
                    self.assertTrue(process.job_empty)
                    self.assertNotEqual(process.returncode,None)
        asyncio.run(run())
