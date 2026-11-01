"""Actual public Flow bridge and original native authority share durable authoring."""
from copy import deepcopy
from pathlib import Path
import tempfile
import uuid
from asgiref.sync import async_to_sync
from django.test import TestCase,override_settings
from django.utils import timezone
from backend.maipa.agent_accounts import ui
from backend.maipa.agent_accounts.models import AgentAccount,SessionReference
from backend.maipa.agent_accounts.references import record_session
from backend.maipa.storage import prepare_directory,prepare_files
from backend.maipa.workflows import drafts,services
from backend.maipa.workflows.models import Definition,WorkflowDraft,WorkflowRun
from tests.test_workflows import fixture
from tests.test_workflow_drafts import graph


class SharedWorkflowToolTests(TestCase):
    def setUp(self):
        self.install,self.owner,self.other,self.member,self.engagement,self.entity=fixture()
        temporary=tempfile.TemporaryDirectory(prefix='maipa-shared-workflow-');self.addCleanup(temporary.cleanup)
        data=Path(temporary.name).resolve()/'install';prepare_directory(data);prepare_files(data)
        settings=override_settings(DATA_ROOT=data);settings.enable();self.addCleanup(settings.disable)
        self.account=AgentAccount.objects.create(owner=self.owner,provider='claude',label='Owned workflow')
        self.reference=record_session(self.owner,str(self.account.pk),'synthetic-workflow-private-reference')
        self.lease=ui.open_ui_lease(self.owner,str(self.account.pk),self.reference)
        self.saved=drafts.create(self.owner,'Shared workflow',str(uuid.uuid4()),entity_id=str(self.entity.pk))
        self.saved=drafts.change(self.owner,self.saved['id'],0,graph())
        self.show(self.saved['id'],0)

    def show(self,identity,revision):
        return ui.dispatch_ui(self.owner,str(self.account.pk),self.lease,{'method':'tools/call','params':{
            'name':'maipa_ui_update' if revision else 'maipa_ui_present','arguments':{'revision':revision,'title':'Workflow',
                'schema':{'id':'shared-workflow','type':'Maipa.Workflow','props':{'draft_id':identity}}}}})

    def broker(self,user=None):
        from backend.maipa.agent_accounts.workflow_tools import WorkflowBroker
        return WorkflowBroker(user or self.owner,str(self.account.pk),self.lease)

    def call(self,broker,tool,**arguments):return async_to_sync(broker.call)(tool,arguments)

    def test_public_tool_catalog_exposes_authoring_without_run_status_or_native_capabilities(self):
        broker=self.broker();names={row['name'] for row in async_to_sync(broker.tools)()}
        self.assertTrue({'flow_get_state','flow_get_node_schema','flow_add_node','flow_delete_node','maipa_workflow_create','maipa_workflow_publish'}.issubset(names))
        self.assertFalse({'flow_run','flow_cancel','flow_set_node_status','flow_list_node_kinds','agent_undo'}.intersection(names))

    def test_create_retry_is_scoped_and_creates_no_execution_principal_grant_or_run(self):
        broker=self.broker();args={'name':'Agent authored','entity_id':str(self.entity.pk),'request_id':'first-authoring'}
        first=self.call(broker,'maipa_workflow_create',**args);self.assertFalse(first.get('isError',False))
        self.assertEqual(self.call(broker,'maipa_workflow_create',**args),first)
        changed=self.call(broker,'maipa_workflow_create',**{**args,'name':'Changed retry'});self.assertTrue(changed['isError'])
        self.assertEqual(WorkflowDraft.objects.count(),2)
        self.assertEqual((Definition.objects.count(),WorkflowRun.objects.count()),(0,0))
        from backend.maipa.policies.models import AgentPrincipal
        self.assertEqual(AgentPrincipal.objects.count(),0)

    def test_actual_public_bridge_awaits_same_owned_draft_and_value_free_receipt(self):
        broker=self.broker();self.call(broker,'flow_get_state')
        result=self.call(broker,'flow_add_node',kind='@particle-academy/transform',label='Added transform',x=400,y=120)
        self.assertFalse(result.get('isError',False))
        saved=drafts.get(self.owner,self.saved['id']);self.assertEqual(saved['revision'],2)
        self.assertEqual(saved['graph']['nodes'][-1]['data']['config'],{'mode':'fields','fields':[{'key':'','value':''}]})
        self.assertEqual(broker.last_activity['draft_id'],self.saved['id'])
        self.assertEqual(broker.last_activity['revision'],2);self.assertNotIn('Added transform',str(broker.last_activity))
        read=self.call(broker,'flow_get_state');self.assertEqual(read['structuredContent'],saved['graph'])
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get('/api/workflows/drafts/'+self.saved['id']+'/',HTTP_X_MAIPA_WORKFLOW_ACTOR=str(self.owner.pk)).json(),saved)

    def test_newer_human_edit_refuses_stale_native_write_until_reread(self):
        broker=self.broker();self.call(broker,'flow_get_state')
        value=deepcopy(self.saved['graph']);value['nodes'][1]['data']['label']='Newer human'
        current=drafts.change(self.owner,self.saved['id'],1,value)
        refused=self.call(broker,'flow_update_node',id=value['nodes'][1]['id'],label='Stale native')
        self.assertTrue(refused['isError']);self.assertEqual(drafts.get(self.owner,self.saved['id']),current);self.assertIsNone(broker.last_activity)
        self.call(broker,'flow_get_state');accepted=self.call(broker,'flow_update_node',id=value['nodes'][1]['id'],label='Native after read')
        self.assertFalse(accepted.get('isError',False));self.assertEqual(drafts.get(self.owner,self.saved['id'])['revision'],3)

    def test_unread_unsafe_forged_status_and_invalid_config_calls_do_not_write(self):
        broker=self.broker();node=self.saved['graph']['nodes'][1]['id']
        self.assertTrue(self.call(broker,'flow_update_node',id=node,label='Unread')['isError'])
        self.call(broker,'flow_get_state')
        for name,args in [('flow_set_node_status',{'id':node,'status':'done'}),
            ('flow_add_node',{'kind':'@particle-academy/terminal_run','label':'Unsafe'}),
            ('flow_add_node',{'kind':'transform','label':'Wrong alias'}),
            ('flow_add_node',{'kind':'@particle-academy/transform','label':'Bad field','config':{'mode':'fields','fields':[{'key':'   ','value':'Do not drop'}]}}),
            ('flow_update_node',{'id':node,'actor_id':str(self.other.pk)})]:
            with self.subTest(name=name,args=args):self.assertTrue(self.call(broker,name,**args)['isError'])
        self.assertEqual(drafts.get(self.owner,self.saved['id']),self.saved);self.assertIsNone(broker.last_activity)

    def test_delete_commits_node_and_all_attached_edges_as_one_revision(self):
        broker=self.broker();self.call(broker,'flow_get_state');node=self.saved['graph']['nodes'][1]['id']
        result=self.call(broker,'flow_delete_node',id=node);self.assertFalse(result.get('isError',False))
        saved=drafts.get(self.owner,self.saved['id']);self.assertEqual((saved['revision'],len(saved['graph']['nodes']),saved['graph']['edges']),(2,2,[]))

    def test_replaced_visible_draft_foreign_actor_and_revoked_session_refuse(self):
        broker=self.broker();self.call(broker,'flow_get_state')
        other=drafts.create(self.owner,'Different workflow',str(uuid.uuid4()));self.show(other['id'],1)
        self.assertTrue(self.call(broker,'flow_update_node',id=self.saved['graph']['nodes'][1]['id'],label='Wrong graph')['isError'])
        self.assertEqual(drafts.get(self.owner,self.saved['id']),self.saved)
        self.assertTrue(self.call(self.broker(self.other),'maipa_workflow_list')['isError'])
        SessionReference.objects.filter(pk=self.reference).update(revoked_at=timezone.now())
        self.assertTrue(self.call(broker,'maipa_workflow_list')['isError'])
        self.assertEqual(WorkflowRun.objects.count(),0)

    def test_publish_records_current_immutable_graph_without_automatic_execution_authority(self):
        broker=self.broker();self.call(broker,'flow_get_state')
        result=self.call(broker,'maipa_workflow_publish',expected_revision=1);self.assertFalse(result.get('isError',False))
        receipt=result['structuredContent'];self.assertEqual(receipt['draft_id'],self.saved['id'])
        self.assertEqual((Definition.objects.count(),WorkflowRun.objects.count()),(1,0))
        self.assertEqual(self.call(broker,'maipa_workflow_publish',expected_revision=1),result)
        self.assertTrue(self.call(broker,'maipa_workflow_publish',expected_revision=0)['isError'])

    def test_publication_requires_current_graph_read_after_a_newer_human_edit(self):
        broker=self.broker();self.call(broker,'flow_get_state')
        graph=deepcopy(self.saved['graph']);graph['nodes'][1]['data']['label']='Human graph not yet read'
        current=drafts.change(self.owner,self.saved['id'],1,graph)
        self.call(broker,'maipa_workflow_list')
        refused=self.call(broker,'maipa_workflow_publish',expected_revision=current['revision'])
        self.assertTrue(refused.get('isError',False),'Metadata revision cannot replace reading the changed graph')
        self.assertEqual((Definition.objects.count(),WorkflowRun.objects.count()),(0,0))
        self.assertEqual(drafts.get(self.owner,self.saved['id']),current)
        self.call(broker,'flow_get_state')
        accepted=self.call(broker,'maipa_workflow_publish',expected_revision=current['revision'])
        self.assertFalse(accepted.get('isError',False))
        self.assertEqual(accepted['structuredContent']['draft_revision'],current['revision'])
        self.assertEqual((Definition.objects.count(),WorkflowRun.objects.count()),(1,0))
