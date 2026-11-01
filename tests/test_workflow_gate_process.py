"""Actual process exit/restart around a parked gate and recorded human answer."""
import json,os,subprocess,sys,tempfile,unittest
from pathlib import Path


class HumanGateProcessTests(unittest.TestCase):
    def call(self,action,*args):
        result=subprocess.run([sys.executable,str(Path(__file__).with_name('workflow_gate_process_helper.py')),
            str(self.database),action,*map(str,args)],env=self.environment,stdin=subprocess.DEVNULL,
            capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr);return json.loads(result.stdout)

    def setUp(self):
        folder=tempfile.TemporaryDirectory(prefix='maipa-gate-process-');self.addCleanup(folder.cleanup)
        self.database=Path(folder.name).resolve()/'synthetic.sqlite3'
        self.environment={k:v for k,v in os.environ.items() if k.upper() in {'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','TEMP','TMP'}}
        self.environment['PYTHONUTF8']='1'

    def test_approval_request_and_recorded_denial_survive_separate_process_exits(self):
        parked=self.call('prepare','human_approval');restored=self.call('read')
        self.assertEqual(restored,parked);self.assertEqual((restored['status'],restored['responses']),('waiting',0))
        self.assertEqual(list(restored['outputs']),['start'])
        recorded=self.call('answer','false');self.assertEqual((recorded['status'],recorded['responses']),('queued',1))
        final=self.call('finish');self.assertEqual((final['status'],final['nodes']['ask']['ports'],final['nodes']['approved']['status']),
            ('completed',['denied'],'skipped'))
        self.assertEqual(final['first_clocks']['ask'],parked['first_clocks']['ask'])
        self.assertEqual((final['nodes']['ask']['attempts'],final['nodes']['start']['attempts']),(2,1))

    def test_recorded_user_values_survive_restart_without_replaying_completed_prefix(self):
        parked=self.call('prepare','user_input');self.call('answer',json.dumps({'answer':'Persisted human value'}))
        final=self.call('finish');again=self.call('read');self.assertEqual(final,again)
        self.assertEqual((final['status'],final['outputs']['result']),('completed',{'answer':'Persisted human value'}))
        self.assertEqual(final['first_clocks']['start'],parked['first_clocks']['start'])
        self.assertEqual(final['nodes']['start']['attempts'],1)
