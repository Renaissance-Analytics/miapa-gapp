"""Recovery must survive actual process exit/termination, not only new objects."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


class WorkflowProcessTests(unittest.TestCase):
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

    def test_separate_workers_resume_committed_checkpoint_without_repeating_it(self):
        initial=self.call('prepare');first=self.call('advance',0);before=self.call('read')
        final=self.call('advance',1);after=self.call('read')
        self.assertEqual((initial['status'],first['status'],final['status']),('queued','running','completed'))
        self.assertEqual(final['outputs'],{'entry':{'name':'Synthetic process'},'answer':{'name':'Synthetic process'}})
        self.assertEqual(after['nodes']['entry']['attempts'],1)
        self.assertEqual(before['first_clocks']['entry'],after['first_clocks']['entry'])

    def test_killed_worker_rolls_back_uncommitted_real_output_and_resumes_safely(self):
        initial=self.call('prepare');marker=self.folder/'uncommitted.txt'
        process=subprocess.Popen(self.command('crash',marker),env=self.environment,stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            deadline=time.monotonic()+15
            while not marker.exists() and process.poll() is None and time.monotonic()<deadline:
                time.sleep(0.05)
            self.assertTrue(marker.exists(),'Worker did not reach the real uncommitted checkpoint')
            self.assertEqual(marker.read_text(encoding='utf-8'),'uncommitted-real-node-output')
            process.terminate();process.wait(timeout=10)
            self.assertIsNotNone(process.returncode)
        finally:
            if process.poll() is None:
                process.kill();process.wait(timeout=10)
            process.communicate(timeout=5)
        recovered=self.call('read')
        self.assertEqual((recovered['status'],recovered['step'],recovered['outputs'],recovered['nodes']),('queued',0,{},{}))
        self.assertEqual(recovered['id'],initial['id'])
        self.call('advance',0);final=self.call('advance',1)
        self.assertEqual((final['status'],final['nodes']['entry']['attempts']),('completed',1))
        self.assertEqual(final['outputs']['answer'],{'name':'Synthetic process'})
