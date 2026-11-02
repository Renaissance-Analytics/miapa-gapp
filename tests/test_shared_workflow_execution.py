"""Public run/cancel bridge must retain the original typed native authority."""
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from backend.maipa.policies.models import AgentPrincipal
from backend.maipa.workflows import native,services,drafts
from backend.maipa.workflows.models import WorkflowRun,NodeCheckpoint
from backend.maipa.workflows.worker import process_job
from tests import test_shared_workflow_tools as authoring_tests
from tests.test_workflow_native import grant
from tests.test_workflow_drafts import graph


class SharedWorkflowExecutionTests(TestCase):
    show=authoring_tests.SharedWorkflowToolTests.show
    broker=authoring_tests.SharedWorkflowToolTests.broker
    call=authoring_tests.SharedWorkflowToolTests.call

    def setUp(self):
        authoring_tests.SharedWorkflowToolTests.setUp(self)
        self.principal=AgentPrincipal.objects.create(user=self.owner,installation=self.install,
            reference='Synthetic explicitly authorized connection',expires_at=timezone.now()+timedelta(hours=1))
        native.bind_principal(self.owner,str(self.account.pk),self.lease,self.principal)
        self.publication=drafts.publish(self.owner,self.saved['id'],1)
        native.prepare_graph(self.owner,self.publication['definition_id'],1)
        self.worker=patch('backend.maipa.workflows.worker.runtime_status',return_value={'available':True,'state':'running'})
        self.worker.start();self.addCleanup(self.worker.stop)

    def test_public_run_accepts_once_only_with_current_original_grant_and_real_output(self):
        broker=self.broker();self.call(broker,'flow_get_state')
        self.assertTrue(self.call(broker,'flow_run')['isError']);self.assertFalse(WorkflowRun.objects.exists())
        grant(self,'run');accepted=self.call(broker,'flow_run');self.assertFalse(accepted.get('isError',False))
        self.assertIn('accepted',accepted['content'][0]['text'].lower())
        self.assertNotIn('outputs',accepted.get('structuredContent',{}))
        self.assertFalse(self.call(broker,'flow_run').get('isError',False));self.assertEqual(WorkflowRun.objects.count(),1)
        row=WorkflowRun.objects.get();self.assertEqual(row.origin_kind,'native')
        for step in range(3):self.assertEqual(process_job(str(row.pk),step),'advanced')
        grant(self,'triage');result=self.call(broker,'maipa_workflow_runs')
        self.assertEqual(result['structuredContent']['runs'][0]['outputs']['result'],'Ready ')
        self.assertEqual({n.attempts for n in NodeCheckpoint.objects.all()},{1})

    def test_public_cancel_needs_current_triage_and_cancel_grants_and_observed_step(self):
        grant(self,'run');broker=self.broker();self.call(broker,'flow_get_state');self.call(broker,'flow_run')
        row=WorkflowRun.objects.get()
        self.assertTrue(self.call(broker,'flow_cancel',runId=str(row.pk))['isError'])
        grant(self,'triage');grant(self,'cancel');self.assertFalse(self.call(broker,'maipa_workflow_runs').get('isError',False))
        self.assertFalse(self.call(broker,'flow_cancel',runId=str(row.pk)).get('isError',False))
        row.refresh_from_db();self.assertEqual(row.status,'cancelled');self.assertFalse(NodeCheckpoint.objects.exists())

    def test_newer_human_graph_or_revoked_principal_refuses_before_dispatch(self):
        grant(self,'run');broker=self.broker();self.call(broker,'flow_get_state')
        drafts.change(self.owner,self.saved['id'],1,graph('Human changed'))
        self.assertTrue(self.call(broker,'flow_run')['isError']);self.assertFalse(WorkflowRun.objects.exists())
        self.call(broker,'flow_get_state');self.call(broker,'maipa_workflow_publish',expected_revision=2)
        self.principal.enabled=False;self.principal.save(update_fields=('enabled',))
        self.assertTrue(self.call(broker,'flow_run')['isError']);self.assertFalse(WorkflowRun.objects.exists())

    def test_worker_stopped_and_forged_origin_fields_create_no_run_or_success(self):
        grant(self,'run');broker=self.broker();self.call(broker,'flow_get_state')
        self.assertTrue(self.call(broker,'flow_run',principal_id=str(self.principal.pk))['isError'])
        with patch('backend.maipa.workflows.worker.runtime_status',return_value={'available':False,'state':'not_running'}):
            self.assertTrue(self.call(broker,'flow_run')['isError'])
        self.assertFalse(WorkflowRun.objects.exists());self.assertIsNone(broker.last_activity)
