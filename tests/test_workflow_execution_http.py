"""Session execution boundaries, independent of model narration or browser runners."""
import json,uuid
from unittest.mock import patch
from django.test import TestCase,Client
from backend.maipa.workflows import drafts,services,gates
from backend.maipa.workflows.models import WorkflowRun,NodeCheckpoint
from backend.maipa.workflows.worker import process_job
from tests.test_workflows import fixture
from tests.test_workflow_drafts import graph


class WorkflowExecutionHttpTests(TestCase):
    def setUp(self):
        self.install,self.owner,self.other,self.member,self.engagement,self.entity=fixture()
        self.draft=drafts.create(self.owner,'Shared execution',str(uuid.uuid4()),entity_id=str(self.entity.pk))
        self.draft=drafts.change(self.owner,self.draft['id'],0,graph())
        self.publication=drafts.publish(self.owner,self.draft['id'],1)
        self.client.force_login(self.owner)
        self.base='/api/workflows/drafts/'+self.draft['id']+'/execution/'
        self.command={'expected_revision':1,'definition_revision':1,'request_key':str(uuid.uuid4()),'seed':{'name':'Ada'}}
        self.worker=patch('backend.maipa.workflows.worker.runtime_status',return_value={'available':True,'state':'running'})
        self.worker.start();self.addCleanup(self.worker.stop)

    def post(self,path=None,command=None,**headers):
        return self.client.post(path or self.base,data=json.dumps(self.command if command is None else command),
            content_type='application/json',HTTP_X_MAIPA_WORKFLOW_ACTOR=str(self.owner.pk),**headers)

    def read(self,path=None):
        return self.client.get(path or self.base,HTTP_X_MAIPA_WORKFLOW_ACTOR=str(self.owner.pk))

    def test_exact_published_revision_runs_real_engine_and_retries_do_not_duplicate(self):
        response=self.post();self.assertEqual(response.status_code,200)
        run=response.json()['run'];self.assertEqual((run['status'],run['definition_revision']),('queued',1))
        self.assertFalse(NodeCheckpoint.objects.exists())
        self.assertEqual(self.post().json()['run']['id'],run['id'])
        self.assertEqual(WorkflowRun.objects.count(),1)
        for step in range(3):self.assertEqual(process_job(run['id'],step),'advanced')
        actual=self.read('/api/workflows/runs/'+run['id']+'/').json()
        self.assertEqual((actual['run']['status'],actual['run']['outputs']['result']),('completed','Ready Ada'))
        self.assertEqual(self.read().json()['runs'][0]['id'],run['id'])
        self.assertEqual({n.attempts for n in NodeCheckpoint.objects.all()},{1})

    def test_stale_unpublished_and_changed_retry_refuse_without_new_execution(self):
        self.assertEqual(self.post(command={**self.command,'definition_revision':2}).status_code,409)
        self.assertEqual(self.post(command={**self.command,'expected_revision':0}).status_code,409)
        self.assertEqual(WorkflowRun.objects.count(),0)
        self.assertEqual(self.post().status_code,200)
        self.assertEqual(self.post(command={**self.command,'seed':{'name':'Changed'}}).status_code,409)
        drafts.change(self.owner,self.draft['id'],1,graph('New human graph'))
        self.assertEqual(self.post(command={**self.command,'expected_revision':2,'request_key':str(uuid.uuid4())}).status_code,409)
        self.assertEqual(WorkflowRun.objects.count(),1)
        self.assertFalse(NodeCheckpoint.objects.exists())

    def test_stopped_worker_refuses_queueing_and_reports_its_actual_state(self):
        with patch('backend.maipa.workflows.worker.runtime_status',return_value={'available':False,'state':'not_running'}):
            self.assertEqual(self.post().status_code,503)
            self.assertEqual(self.read().json()['worker'],{'available':False,'state':'not_running'})
        self.assertFalse(WorkflowRun.objects.exists())

    def test_foreign_actor_scope_and_revoked_role_cannot_read_or_run(self):
        run=self.post().json()['run'];url='/api/workflows/runs/'+run['id']+'/'
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url,HTTP_X_MAIPA_WORKFLOW_ACTOR=str(self.other.pk)).status_code,404)
        self.assertEqual(self.client.post(self.base,data=json.dumps(self.command),content_type='application/json',
            HTTP_X_MAIPA_WORKFLOW_ACTOR=str(self.other.pk)).status_code,404)
        self.assertEqual(self.read().status_code,409)
        self.client.force_login(self.owner);self.member.enabled=False;self.member.save()
        self.assertIn(self.read(url).status_code,(403,404))
        self.assertEqual(WorkflowRun.objects.count(),1)

    def test_cancel_requires_observed_step_and_prevents_all_future_nodes(self):
        run=self.post().json()['run'];url='/api/workflows/runs/'+run['id']+'/cancel/'
        self.assertEqual(self.post(url,{'expected_step':1}).status_code,409)
        self.assertEqual(self.post(url,{'expected_step':0}).json()['run']['status'],'cancelled')
        self.assertEqual(process_job(run['id'],0),'stale')
        self.assertFalse(NodeCheckpoint.objects.exists())
        self.assertEqual(self.read().json()['runs'][0]['status'],'cancelled')

    def test_execution_endpoints_require_session_csrf_and_closed_command_shapes(self):
        anonymous=Client();self.assertEqual(anonymous.get(self.base).status_code,401)
        guarded=Client(enforce_csrf_checks=True);guarded.force_login(self.owner)
        self.assertEqual(guarded.post(self.base,data=json.dumps(self.command),content_type='application/json',
            HTTP_X_MAIPA_WORKFLOW_ACTOR=str(self.owner.pk)).status_code,403)
        self.assertEqual(self.post(command={**self.command,'actor_id':str(self.owner.pk)}).status_code,400)
        self.assertEqual(self.client.get(self.base).status_code,409)
        self.assertEqual(self.read(self.base+'?owner='+str(self.owner.pk)).status_code,400)
        self.assertFalse(WorkflowRun.objects.exists())

    def test_pending_gate_is_real_and_human_answer_resumes_exact_original_run(self):
        value=graph();value['nodes'][1]['type']='human_approval';value['nodes'][1]['data']={
            'kind':'human_approval','label':'Review','config':{'title':'Approve synthetic output'}}
        value['edges'][1]['sourceHandle']='approved'
        saved=drafts.change(self.owner,self.draft['id'],1,value)
        drafts.publish(self.owner,saved['id'],saved['revision'])
        self.command.update(expected_revision=2,definition_revision=2,seed={'approved':True})
        run=self.post().json()['run']
        for step in range(2):self.assertEqual(process_job(run['id'],step),'advanced')
        value=self.read('/api/workflows/runs/'+run['id']+'/').json()
        self.assertEqual((value['run']['status'],value['gate']['node_id']),('waiting','format'))
        self.assertNotIn('result',value['run']['outputs'])
        gate=value['gate'];command={'expected_step':gate['step'],'definition_revision':2,'request_sha256':gate['request_sha256'],
            'request_key':str(uuid.uuid4()),'answer':True}
        url='/api/workflows/runs/'+run['id']+'/gates/'+gate['id']+'/response/'
        self.assertEqual(self.post(url,command).json()['status'],'recorded')
        for step in range(3,6):
            current=services.get_run(self.owner,run['id'])
            if current['status']=='completed':break
            self.assertEqual(process_job(run['id'],current['step']),'advanced')
        final=self.read('/api/workflows/runs/'+run['id']+'/').json()
        self.assertEqual(final['run']['status'],'completed');self.assertIsNone(final['gate'])
        self.assertEqual(NodeCheckpoint.objects.get(run_id=run['id'],node_id='start').attempts,1)
