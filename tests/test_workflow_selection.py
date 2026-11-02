"""Shared run navigation cannot confer execution authority or rewrite history."""
import json,uuid
from django.test import TestCase,Client,TransactionTestCase
from django.utils import timezone
from backend.maipa.agent_accounts.models import SessionReference
from backend.maipa.workflows import drafts,services,execution
from backend.maipa.workflows.models import WorkflowRun,NativeRunAuthority
from backend.maipa.workflows.worker import process_job
from tests import test_shared_workflow_execution as existing
from tests.test_workflow_native import grant
from tests.test_workflow_drafts import graph


class WorkflowSelectionTests(TestCase):
    show=existing.SharedWorkflowExecutionTests.show
    broker=existing.SharedWorkflowExecutionTests.broker
    call=existing.SharedWorkflowExecutionTests.call
    def setUp(self):
        existing.SharedWorkflowExecutionTests.setUp(self)
        self.base=f'/api/workflows/drafts/{self.saved["id"]}/selection/'
        self.client.force_login(self.owner)
    def launch(self,key=None):
        return services.dispatch(self.owner,self.publication['definition_id'],1,{'name':'Ada'},key or str(uuid.uuid4()))
    def post(self,run_id,revision=0,show_details=True,**extra):
        return self.client.post(self.base,data=json.dumps({'expected_revision':revision,'run_id':run_id,'show_details':show_details,**extra}),
            content_type='application/json',HTTP_X_MAIPA_WORKFLOW_ACTOR=str(self.owner.pk))

    def test_human_and_native_select_the_same_saved_run_with_revision_conflicts(self):
        first=self.launch();second=self.launch();grant(self,'triage')
        response=self.post(first['id']);self.assertEqual(response.status_code,200)
        selection=response.json();self.assertEqual(selection,{'revision':1,'run_id':first['id'],'show_details':True,'source':'human'})
        broker=self.broker();state=self.call(broker,'maipa_workflow_runs')['structuredContent']
        self.assertEqual(state['selection'],selection);self.assertEqual({r['id'] for r in state['runs']},{first['id'],second['id']})
        result=self.call(broker,'maipa_workflow_select_run',run_id=second['id'],expected_revision=1,show_details=False)
        self.assertFalse(result.get('isError',False));ack=result['structuredContent']
        self.assertEqual(ack,{'revision':2,'run_id':second['id'],'show_details':False,'source':'agent'})
        self.assertEqual(execution.snapshot(self.owner,self.saved['id'])['selection'],ack)
        self.assertEqual(self.post(first['id'],1).status_code,409)
        self.assertEqual(execution.snapshot(self.owner,self.saved['id'])['selection'],ack)
        self.assertEqual(WorkflowRun.objects.count(),2)

    def test_native_dispatch_selects_actual_receipt_and_opens_details(self):
        grant(self,'run');broker=self.broker();self.call(broker,'flow_get_state')
        self.assertFalse(self.call(broker,'flow_run').get('isError',False))
        row=WorkflowRun.objects.get();selected=execution.snapshot(self.owner,self.saved['id'])['selection']
        self.assertEqual(selected,{'revision':1,'run_id':str(row.pk),'show_details':True,'source':'agent'})
        self.assertTrue(self.call(broker,'maipa_workflow_select_run',run_id=str(row.pk),expected_revision=1,show_details=False)['isError'])
        self.assertEqual(WorkflowRun.objects.count(),1)

    def test_human_run_inspection_requires_agent_triage_and_never_adopts_execution(self):
        row=self.launch();broker=self.broker()
        self.assertTrue(self.call(broker,'maipa_workflow_runs')['isError'])
        grant(self,'triage');grant(self,'cancel');result=self.call(broker,'maipa_workflow_runs')
        self.assertFalse(result.get('isError',False));self.assertEqual(result['structuredContent']['runs'][0]['id'],row['id'])
        self.call(broker,'flow_get_state')
        self.assertTrue(self.call(broker,'flow_cancel',runId=row['id'])['isError'])
        self.assertFalse(NativeRunAuthority.objects.exists());self.assertEqual(WorkflowRun.objects.get().status,'queued')

    def test_foreign_different_workflow_csrf_and_actor_hints_cannot_select(self):
        row=self.launch();other=drafts.create(self.owner,'Another workflow',str(uuid.uuid4()),entity_id=str(self.entity.pk))
        other=drafts.change(self.owner,other['id'],0,graph());pub=drafts.publish(self.owner,other['id'],1)
        unrelated=services.dispatch(self.owner,pub['definition_id'],1,{},str(uuid.uuid4()))
        self.assertEqual(self.post(unrelated['id']).status_code,404)
        self.assertEqual(self.post(row['id'],source='agent').status_code,400)
        anonymous=Client();self.assertEqual(anonymous.post(self.base).status_code,401)
        guarded=Client(enforce_csrf_checks=True);guarded.force_login(self.owner)
        self.assertEqual(guarded.post(self.base,data='{}',content_type='application/json').status_code,403)
        self.client.force_login(self.other);self.assertEqual(self.post(row['id']).status_code,409)

    def test_newer_human_navigation_and_revoked_session_refuse_stale_native_selection(self):
        first=self.launch();second=self.launch();grant(self,'triage');broker=self.broker()
        self.call(broker,'maipa_workflow_runs');self.assertEqual(self.post(second['id']).status_code,200)
        self.assertTrue(self.call(broker,'maipa_workflow_select_run',run_id=first['id'],expected_revision=0,show_details=True)['isError'])
        self.call(broker,'maipa_workflow_runs')
        SessionReference.objects.filter(pk=self.reference).update(revoked_at=timezone.now())
        self.assertTrue(self.call(broker,'maipa_workflow_select_run',run_id=first['id'],expected_revision=1,show_details=True)['isError'])
        self.assertEqual(execution.snapshot(self.owner,self.saved['id'])['selection']['run_id'],second['id'])

    def test_older_selected_run_is_retained_in_bounded_history_and_details_survive_readback(self):
        first=self.launch()
        for _ in range(11):self.launch()
        self.assertEqual(self.post(first['id']).status_code,200)
        state=execution.snapshot(self.owner,self.saved['id'])
        self.assertEqual(len(state['runs']),10);self.assertIn(first['id'],{r['id'] for r in state['runs']})
        self.assertEqual(state['selection']['run_id'],first['id']);self.assertTrue(state['selection']['show_details'])

    def test_actual_failed_human_run_can_be_triaged_repaired_and_run_as_a_new_immutable_version(self):
        faulty=drafts.change(self.owner,self.saved['id'],1,graph('{{ in.name }}'*120));pub=drafts.publish(self.owner,self.saved['id'],2)
        failed=services.dispatch(self.owner,pub['definition_id'],2,{'name':'Synthetic expansion '*50},str(uuid.uuid4()))
        self.assertEqual(process_job(failed['id'],0),'advanced');self.assertEqual(process_job(failed['id'],1),'advanced')
        original=services.get_run(self.owner,failed['id']);self.assertEqual((original['status'],original['error']),('failed','output_limit'))
        grant(self,'triage');grant(self,'run');broker=self.broker()
        observed=self.call(broker,'maipa_workflow_runs')['structuredContent'];self.assertEqual(observed['runs'][0],original)
        self.call(broker,'flow_get_state')
        self.assertFalse(self.call(broker,'flow_update_node',id='format',config={'expression':'Repaired workflow verified'}).get('isError',False))
        self.call(broker,'maipa_workflow_publish',expected_revision=3);self.call(broker,'flow_get_state')
        self.assertFalse(self.call(broker,'flow_run').get('isError',False))
        repaired=WorkflowRun.objects.exclude(pk=failed['id']).get()
        for step in range(3):self.assertEqual(process_job(str(repaired.pk),step),'advanced')
        self.assertEqual(services.get_run(self.owner,str(repaired.pk))['outputs']['result'],'Repaired workflow verified')
        self.assertEqual(services.get_run(self.owner,failed['id']),original)
        self.assertEqual(repaired.version.revision,3)

    def test_repeated_dispatch_does_not_steal_a_newer_human_selection(self):
        from backend.maipa.workflows import execution
        first=self.launch();key=str(uuid.uuid4())
        args={'expected_revision':1,'definition_revision':1,'request_key':key,'seed':{}}
        accepted=execution.dispatch(self.owner,self.saved['id'],**args)
        chosen=self.post(first['id'],accepted['selection']['revision']).json()
        retried=execution.dispatch(self.owner,self.saved['id'],**args)
        self.assertEqual(retried['run']['id'],accepted['run']['id']);self.assertEqual(retried['selection'],chosen)
        grant(self,'run');broker=self.broker();self.call(broker,'flow_get_state');self.call(broker,'flow_run')
        current=execution.snapshot(self.owner,self.saved['id'])['selection'];self.assertEqual(self.post(first['id'],current['revision']).status_code,200)
        chosen=execution.snapshot(self.owner,self.saved['id'])['selection'];self.call(broker,'flow_run')
        self.assertEqual(execution.snapshot(self.owner,self.saved['id'])['selection'],chosen)
        self.assertEqual(WorkflowRun.objects.count(),3)


class WorkflowSelectionMigrationTests(TransactionTestCase):
    def test_downgrade_refuses_before_erasing_saved_human_navigation(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        from backend.maipa.workflows import selection
        from tests.test_workflows import fixture
        _,owner,_,_,_,entity=fixture()
        row=drafts.create(owner,'Saved selection',str(uuid.uuid4()),entity_id=str(entity.pk))
        row=drafts.change(owner,row['id'],0,graph());publication=drafts.publish(owner,row['id'],1)
        run=services.dispatch(owner,publication['definition_id'],1,{},str(uuid.uuid4()))
        chosen=selection.change(owner,row['id'],expected_revision=0,run_id=run['id'],show_details=True)
        with self.assertRaisesRegex(RuntimeError,'Saved workflow navigation requires offline backup recovery'):
            MigrationExecutor(connection).migrate([('workflows','0005_shared_authoring')])
        self.assertEqual(selection.read(owner,row['id']),chosen)
        self.assertEqual(services.get_run(owner,run['id'])['status'],'queued')
        self.assertIn(('workflows','0006_workflowselection'),MigrationExecutor(connection).loader.applied_migrations)
