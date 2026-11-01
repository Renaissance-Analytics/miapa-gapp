"""Finite actual-process dispatch, kill and historical migration acceptance."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


class WorkflowWorkerProcessTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='maipa-flow-process-')
        self.addCleanup(temporary.cleanup)
        self.folder=Path(temporary.name).resolve();self.database=self.folder/'synthetic.sqlite3'
        self.helper=Path(__file__).with_name('workflow_process_helper.py')
        self.environment={k:v for k,v in os.environ.items() if k.upper() in {'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','TEMP','TMP'}}
        self.environment['PYTHONUTF8']='1'

    def command(self,action,*arguments):
        return [sys.executable,str(self.helper),str(self.database),action,*map(str,arguments)]

    def call(self,action,*arguments):
        result=subprocess.run(self.command(action,*arguments),env=self.environment,stdin=subprocess.DEVNULL,
            capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        return json.loads(result.stdout)

    def test_racing_processes_execute_one_observed_step_and_restart_keeps_checkpoint(self):
        self.call('prepare');gate=self.folder/'go';markers=[self.folder/'ready-a',self.folder/'ready-b']
        workers=[subprocess.Popen(self.command('worker',0,marker,gate),env=self.environment,
            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for marker in markers]
        try:
            deadline=time.monotonic()+15
            while not all(marker.exists() for marker in markers) and time.monotonic()<deadline: time.sleep(0.02)
            self.assertTrue(all(marker.exists() for marker in markers))
            gate.write_text('go',encoding='utf-8')
            outcomes=[]
            for process in workers:
                stdout,stderr=process.communicate(timeout=30)
                self.assertEqual(process.returncode,0,stderr);outcomes.append(json.loads(stdout)['outcome'])
        finally:
            for process in workers:
                if process.poll() is None: process.kill()
                process.communicate(timeout=5)
        self.assertEqual(sorted(outcomes),['advanced','stale'])
        before=self.call('read')
        self.assertEqual((before['step'],list(before['outputs']),before['nodes']['entry']['attempts']),(1,['entry'],1))
        self.assertEqual(self.call('worker',1),{'outcome':'advanced'})
        final=self.call('read')
        self.assertEqual((final['status'],final['outputs']['answer']),('completed',{'name':'Synthetic process'}))
        self.assertEqual(final['first_clocks']['entry'],before['first_clocks']['entry'])
        self.assertEqual(self.call('worker',2),{'outcome':'stale'})

    def test_killed_scoped_worker_leaves_no_uncommitted_claim_or_output(self):
        self.call('prepare');marker=self.folder/'interrupted'
        process=subprocess.Popen(self.command('crash-worker',marker),env=self.environment,stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            deadline=time.monotonic()+15
            while not marker.exists() and process.poll() is None and time.monotonic()<deadline: time.sleep(0.02)
            self.assertTrue(marker.exists(),'Worker did not reach its real uncommitted output')
            self.assertEqual(marker.read_text(encoding='utf-8'),'uncommitted-real-node-output')
            process.terminate();process.wait(timeout=10)
        finally:
            if process.poll() is None: process.kill()
            process.communicate(timeout=5)
        recovered=self.call('read')
        self.assertEqual((recovered['status'],recovered['step'],recovered['outputs'],recovered['nodes']),('queued',0,{},{}))
        self.call('worker',0);self.call('worker',1)
        self.assertEqual(self.call('read')['nodes']['entry']['attempts'],1)

    def test_legacy_migration_preserves_private_records_without_inventing_origin(self):
        result=self.call('legacy')
        self.assertEqual(result['ids'],result['expected_ids'])
        self.assertEqual(result['states'],{'completed':['completed',None,None,None,None],
            'queued':['blocked','origin_unverified',None,None,None],'running':['blocked','origin_unverified',None,None,None]})
        self.assertEqual(result['seeds'],[{'private':'legacy seed'}]*3)
        self.assertEqual(result['outputs'],[{'private':'legacy output'}]*3)
        self.assertEqual(result['queue'],{'observed':0,'advanced':0,'blocked':0,'stale':0})
