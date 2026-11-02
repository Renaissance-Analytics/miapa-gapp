"""Human authorization of an original connection, never model-supplied authority."""
import json,uuid
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase,Client
from django.utils import timezone
from backend.maipa.agent_accounts.models import UiComposition,SessionReference
from backend.maipa.policies.models import AgentPrincipal,AuthorityGrant,PolicyAudit
from backend.maipa.workflows import drafts
from backend.maipa.workflows.models import NativePrincipalBinding,WorkflowRun,NodeCheckpoint
from backend.maipa.workflows.worker import process_job
from tests import test_shared_workflow_tools as authoring


class WorkflowPermissionTests(TestCase):
    show=authoring.SharedWorkflowToolTests.show
    broker=authoring.SharedWorkflowToolTests.broker
    call=authoring.SharedWorkflowToolTests.call

    def setUp(self):
        authoring.SharedWorkflowToolTests.setUp(self)
        # Independent synthetic administrator fixture, not a production promotion.
        self.member.role='administrator';self.member.save(update_fields=('role',))
        UiComposition.objects.filter(pk=self.lease).update(role='administrator')
        self.base=f'/api/workflows/drafts/{self.saved["id"]}/agent-authority/{self.account.pk}/{self.lease}/'
        self.client.force_login(self.owner)
        self.command={'expected_revision':1,'expected_policy_version':0,'enabled':True}

    def read(self):
        return self.client.get(self.base,HTTP_X_MAIPA_WORKFLOW_ACTOR=str(self.owner.pk))

    def post(self,command=None):
        return self.client.post(self.base,data=json.dumps(self.command if command is None else command),
            content_type='application/json',HTTP_X_MAIPA_WORKFLOW_ACTOR=str(self.owner.pk))

    def test_human_admin_records_one_original_connection_and_exact_nonfinancial_grants(self):
        before=self.read();self.assertEqual(before.status_code,200)
        self.assertFalse(before.json()['enabled']);self.assertTrue(before.json()['can_manage'])
        self.assertEqual((AgentPrincipal.objects.count(),AuthorityGrant.objects.count()),(0,0))
        response=self.post();self.assertEqual(response.status_code,200);value=response.json()
        self.assertEqual((value['draft_id'],value['account_id'],value['ui_id'],value['entity_id']),
            (self.saved['id'],str(self.account.pk),self.lease,str(self.entity.pk)))
        self.assertTrue(value['enabled']);self.assertEqual(value['policy_version'],1)
        binding=NativePrincipalBinding.objects.get();principal=AgentPrincipal.objects.get()
        self.assertEqual((binding.owner_id,binding.account_id,binding.ui_id,binding.session_id,binding.agent_id),
            (self.owner.pk,self.account.pk,uuid.UUID(self.lease),uuid.UUID(self.reference),principal.pk))
        self.assertTrue(timezone.now()<principal.expires_at<=timezone.now()+timedelta(hours=1))
        rows=list(AuthorityGrant.objects.all())
        self.assertEqual({(g.action,g.duty) for g in rows},{('workflow.run','executor'),('workflow.cancel','executor'),
            ('workflow.resume','executor'),('workflow.triage','reviewer')})
        self.assertTrue(all(g.agent_id==principal.pk and g.subject_id==self.owner.pk and g.entity_id==self.entity.pk
            and not g.can_delegate and g.limit is None and g.currency is None and g.expires_at==principal.expires_at for g in rows))
        self.assertTrue(PolicyAudit.objects.filter(actor=f'human:{self.owner.pk}',operation='workflow.authority.enable').exists())
        self.assertFalse(WorkflowRun.objects.exists())
        for secret in ('private_handle','storage_id','agent_id','binding_id','grant_ids'):
            self.assertNotIn(secret,response.content.decode())

    def test_retry_and_stale_policy_do_not_mint_duplicate_authority(self):
        first=self.post();self.assertEqual(first.status_code,200)
        self.assertEqual(self.post().status_code,409)
        latest=first.json()['policy_version']
        self.assertEqual(self.post({**self.command,'expected_policy_version':latest}).status_code,200)
        self.assertEqual((AgentPrincipal.objects.count(),NativePrincipalBinding.objects.count(),AuthorityGrant.objects.count()),(1,1,4))
        self.assertEqual(self.post({**self.command,'expected_policy_version':latest,'expected_revision':0}).status_code,409)

    def test_model_catalog_and_forged_actor_financial_expiry_fields_cannot_grant(self):
        broker=self.broker()
        refused=self.call(broker,'maipa_workflow_authorize',enabled=True)
        self.assertTrue(refused['isError'])
        for hint in ({'agent_id':str(uuid.uuid4())},{'expires_at':'2099-01-01T00:00:00Z'},
                {'actions':['ledger.post']},{'actor_kind':'human'},{'entity_id':str(uuid.uuid4())}):
            self.assertEqual(self.post({**self.command,**hint}).status_code,400)
        self.assertEqual((AgentPrincipal.objects.count(),AuthorityGrant.objects.count()),(0,0))

    def test_foreign_admin_changed_role_and_hidden_workflow_cannot_change_authority(self):
        self.client.force_login(self.other);self.assertIn(self.post().status_code,(404,409))
        self.client.force_login(self.owner)
        self.member.role='preparer';self.member.save(update_fields=('role',))
        UiComposition.objects.filter(pk=self.lease).update(role='preparer')
        self.assertFalse(self.read().json()['can_manage']);self.assertEqual(self.post().status_code,404)
        self.member.role='administrator';self.member.save(update_fields=('role',))
        UiComposition.objects.filter(pk=self.lease).update(role='administrator')
        other=drafts.create(self.owner,'Different visible workflow',str(uuid.uuid4()),entity_id=str(self.entity.pk))
        self.show(other['id'],1)
        self.assertEqual(self.post().status_code,404)
        self.assertFalse(AgentPrincipal.objects.exists())

    def test_session_csrf_current_actor_and_revoked_original_binding_are_required(self):
        anonymous=Client();self.assertEqual(anonymous.get(self.base).status_code,401)
        guarded=Client(enforce_csrf_checks=True);guarded.force_login(self.owner)
        self.assertEqual(guarded.post(self.base,data=json.dumps(self.command),content_type='application/json',
            HTTP_X_MAIPA_WORKFLOW_ACTOR=str(self.owner.pk)).status_code,403)
        self.assertEqual(self.client.get(self.base).status_code,409)
        SessionReference.objects.filter(pk=self.reference).update(revoked_at=timezone.now())
        self.assertEqual(self.post().status_code,404);self.assertFalse(AgentPrincipal.objects.exists())

    def test_revocation_stops_next_real_node_and_reenable_cannot_rewrite_run_origin(self):
        enabled=self.post();self.assertEqual(enabled.status_code,200)
        drafts.publish(self.owner,self.saved['id'],1);broker=self.broker();self.call(broker,'flow_get_state')
        with patch('backend.maipa.workflows.worker.runtime_status',return_value={'available':True,'state':'running'}):
            self.assertFalse(self.call(broker,'flow_run').get('isError',False))
        run=WorkflowRun.objects.get();self.assertEqual(process_job(str(run.pk),0),'advanced')
        disabled=self.post({**self.command,'expected_policy_version':enabled.json()['policy_version'],'enabled':False})
        self.assertEqual(disabled.status_code,200);self.assertFalse(disabled.json()['enabled'])
        self.assertEqual(process_job(str(run.pk),1),'blocked')
        self.assertEqual(NodeCheckpoint.objects.filter(run=run).count(),1)
        again=self.post({**self.command,'expected_policy_version':disabled.json()['policy_version']})
        self.assertEqual(again.status_code,200);self.assertTrue(again.json()['enabled'])
        self.assertEqual(NativePrincipalBinding.objects.count(),1)
        self.assertTrue(self.call(broker,'maipa_workflow_runs')['isError'])
        self.assertEqual(NodeCheckpoint.objects.filter(run=run).count(),1)

    def test_expired_principal_cannot_be_extended_or_transferred_to_a_new_session(self):
        self.assertEqual(self.post().status_code,200)
        AgentPrincipal.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.post({**self.command,'expected_policy_version':1}).status_code,404)
        self.assertEqual((AgentPrincipal.objects.count(),NativePrincipalBinding.objects.count()),(1,1))

    def test_agent_discovers_owned_client_names_without_guessing_ids_or_creating_authority(self):
        from backend.maipa.intake.models import Engagement,Entity
        foreign=Engagement.objects.create(owner=self.other,installation=self.install,name='Private other client')
        Entity.objects.create(engagement=foreign,name='Private other entity')
        value=self.call(self.broker(),'maipa_workflow_list')['structuredContent']
        self.assertEqual(value['entities'],[{'id':str(self.entity.pk),'name':self.entity.name,'client_name':self.engagement.name}])
        self.assertNotIn('Private other',json.dumps(value))
        self.assertFalse(AgentPrincipal.objects.exists());self.assertFalse(AuthorityGrant.objects.exists())
