"""Original native provenance and current policy, not an ACP readiness claim."""
from datetime import timedelta
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from backend.maipa.agent_accounts.models import AgentAccount, SessionReference
from backend.maipa.agent_accounts.references import record_session
from backend.maipa.agent_accounts.ui import open_ui_lease
from backend.maipa.policies import authority
from backend.maipa.policies.models import AgentPrincipal, PolicyAudit, PolicyResource
from backend.maipa.storage import prepare_directory, prepare_files
from backend.maipa.workflows import services as flows
from backend.maipa.workflows.models import WorkflowRun, NodeCheckpoint
from backend.maipa.workflows.worker import process_job
from tests.test_workflows import fixture, graph


def native_fixture(case):
    case.install,case.owner,case.other,case.member,case.engagement,case.entity=fixture()
    temporary=tempfile.TemporaryDirectory(prefix='maipa-native-flow-');case.addCleanup(temporary.cleanup)
    case.data=prepare_directory(Path(temporary.name).resolve()/'install');prepare_files(case.data)
    configured=override_settings(DATA_ROOT=case.data);configured.enable();case.addCleanup(configured.disable)
    case.account=AgentAccount.objects.create(owner=case.owner,provider='claude',label='Synthetic native account')
    case.session=record_session(case.owner,str(case.account.pk),'synthetic-native-protocol-handle')
    case.ui=open_ui_lease(case.owner,str(case.account.pk),case.session)
    case.principal=AgentPrincipal.objects.create(user=case.owner,installation=case.install,
        reference='synthetic-original-connection',expires_at=timezone.now()+timedelta(hours=1))
    case.saved=flows.save_definition(case.owner,'Native deterministic workflow',graph(),entity_id=str(case.entity.pk))


def grant(case,operation,*,agent=True):
    return authority.provision_root(entity_id=case.entity.pk,subject_id=case.owner.pk,
        agent_id=case.principal.pk if agent else None,action='workflow.'+operation,
        duty='reviewer' if operation=='triage' else 'executor',starts_at=timezone.now()-timedelta(minutes=1),
        expires_at=timezone.now()+timedelta(minutes=30),limit=None,currency=None,evidence='Synthetic explicit workflow authority')


class NativeWorkflowTests(TestCase):
    def setUp(self):
        native_fixture(self)

    def api(self):
        from backend.maipa.workflows import native
        return native

    def actor(self):
        binding=self.api().bind_principal(self.owner,str(self.account.pk),self.ui,self.principal)
        return self.api().actor(self.owner,binding)

    def launch(self,actor=None,key='native-delivery'):
        actor=actor or self.actor()
        self.api().prepare_graph(self.owner,self.saved['id'],1)
        with patch('backend.maipa.workflows.worker.runtime_status',return_value={'available':True,'state':'running'},create=True):
            return self.api().dispatch(actor,self.saved['id'],1,{'name':'Ada'},key)

    def test_native_origin_is_immutable_and_real_engine_checks_recorded_policy_each_node(self):
        from backend.maipa.workflows.models import NativeRunAuthority
        actor=self.actor();run_grant=grant(self,'run');run=self.launch(actor)
        origin=NativeRunAuthority.objects.select_related('binding').get(run_id=run['id'])
        self.assertEqual((origin.binding.owner_id,origin.binding.account_id,str(origin.binding.session_id),str(origin.binding.ui_id)),
                         (self.owner.pk,self.account.pk,self.session,self.ui))
        self.assertEqual((origin.binding.agent_id,origin.grant_id),(self.principal.pk,run_grant.pk))
        self.assertEqual(WorkflowRun.objects.get(pk=run['id']).origin_kind,'native')
        with self.assertRaises(ValueError):origin.save()
        with self.assertRaises(ValueError):origin.binding.delete()
        for step in range(3):self.assertEqual(process_job(run['id'],step),'advanced')
        result=flows.get_run(self.owner,run['id'])
        self.assertEqual((result['status'],result['outputs']['result']),('completed','Ready Ada'))
        self.assertEqual({row.attempts for row in NodeCheckpoint.objects.all()},{1})
        checks=PolicyAudit.objects.filter(actor='agent:'+str(self.principal.pk),operation='workflow.check',allowed=True)
        self.assertGreaterEqual(checks.count(),4)
        self.assertNotIn('synthetic-native-protocol-handle',str(run)+str(result))

    def test_native_dispatch_needs_exact_agent_grant_and_never_borrows_human_authority(self):
        actor=self.actor()
        with self.assertRaises(flows.Unavailable):self.launch(actor)
        grant(self,'run',agent=False)
        with self.assertRaises(flows.Unavailable):self.launch(actor)
        self.assertEqual(WorkflowRun.objects.count(),0)
        grant(self,'run');self.assertEqual(self.launch(actor)['status'],'queued')

    def test_forged_actor_and_foreign_native_registration_refuse_without_run(self):
        actor=self.actor();grant(self,'run')
        with self.assertRaises(flows.Unavailable):self.launch({'binding':str(self.principal.pk),'user':self.owner.pk})
        with self.assertRaises(flows.Unavailable):self.api().actor(self.other,actor.binding_id)
        foreign=AgentPrincipal.objects.create(user=self.other,installation=self.install,
            reference='synthetic-foreign-principal',expires_at=timezone.now()+timedelta(hours=1))
        with self.assertRaises(flows.Unavailable):self.api().bind_principal(self.owner,str(self.account.pk),self.ui,foreign)
        self.assertEqual(WorkflowRun.objects.count(),0)

    def test_same_principal_cannot_silently_bind_to_another_session_or_account(self):
        original=self.actor()
        other_session=record_session(self.owner,str(self.account.pk),'synthetic-second-protocol-handle')
        other_ui=open_ui_lease(self.owner,str(self.account.pk),other_session)
        with self.assertRaises(flows.Conflict):self.api().bind_principal(self.owner,str(self.account.pk),other_ui,self.principal)
        self.assertEqual(self.actor().binding_id,original.binding_id)

    def test_duplicate_native_delivery_is_exact_and_another_context_cannot_adopt_it(self):
        original=self.actor();grant(self,'run');run=self.launch(original)
        self.assertEqual(self.launch(original)['id'],run['id'])
        second=AgentPrincipal.objects.create(user=self.owner,installation=self.install,
            reference='synthetic-second-agent',expires_at=timezone.now()+timedelta(hours=1))
        binding=self.api().bind_principal(self.owner,str(self.account.pk),self.ui,second)
        second_actor=self.api().actor(self.owner,binding)
        previous=self.principal;self.principal=second;grant(self,'run');self.principal=previous
        with self.assertRaises(flows.Conflict):self.launch(second_actor)
        with self.assertRaises(flows.Conflict):flows.dispatch(self.owner,self.saved['id'],1,{'name':'Ada'},'native-delivery')
        self.assertEqual(WorkflowRun.objects.count(),1)

    def test_grant_revocation_blocks_before_next_node_and_replacement_grant_does_not_adopt(self):
        actor=self.actor();original_grant=grant(self,'run');run=self.launch(actor)
        self.assertEqual(process_job(run['id'],0),'advanced')
        authority.operator_revoke(original_grant.pk,'Synthetic revocation');grant(self,'run');grant(self,'resume')
        self.assertEqual(process_job(run['id'],1),'blocked')
        stopped=flows.get_run(self.owner,run['id'])
        self.assertEqual((stopped['status'],stopped['error'],list(stopped['outputs'])),('blocked','native_authority_revoked',['start']))
        with self.assertRaises(flows.Unavailable):self.api().resume(actor,run['id'],stopped['step'])
        self.assertEqual(NodeCheckpoint.objects.count(),1)

    def test_restored_original_principal_requires_explicit_native_resume_and_keeps_checkpoint(self):
        actor=self.actor();grant(self,'run');grant(self,'resume');run=self.launch(actor)
        process_job(run['id'],0);first=NodeCheckpoint.objects.get().first_attempt_at
        AgentPrincipal.objects.filter(pk=self.principal.pk).update(enabled=False)
        self.assertEqual(process_job(run['id'],1),'blocked')
        AgentPrincipal.objects.filter(pk=self.principal.pk).update(enabled=True)
        with self.assertRaises(flows.Unavailable):flows.resume(self.owner,run['id'],2)
        with self.assertRaises(flows.Conflict):self.api().resume(actor,run['id'],1)
        with patch('backend.maipa.workflows.worker.runtime_status',return_value={'available':True,'state':'running'},create=True):
            resumed=self.api().resume(actor,run['id'],2)
        self.assertEqual((resumed['status'],resumed['step']),('queued',3))
        self.assertEqual(process_job(run['id'],2),'stale')
        process_job(run['id'],3);process_job(run['id'],4)
        self.assertEqual(flows.get_run(self.owner,run['id'])['outputs']['result'],'Ready Ada')
        checkpoint=NodeCheckpoint.objects.get(node_id='start')
        self.assertEqual((checkpoint.attempts,checkpoint.first_attempt_at),(1,first))

    def test_revoked_session_and_changed_ui_authority_cannot_survive_in_saved_actor(self):
        actor=self.actor();grant(self,'run');run=self.launch(actor)
        SessionReference.objects.filter(pk=self.session).update(revoked_at=timezone.now())
        self.assertEqual(process_job(run['id'],0),'blocked')
        with self.assertRaises(flows.Unavailable):self.launch(actor,key='second')
        self.assertFalse(NodeCheckpoint.objects.exists())

    def test_changed_principal_expiry_cannot_extend_original_native_binding(self):
        actor=self.actor();grant(self,'run');run=self.launch(actor)
        AgentPrincipal.objects.filter(pk=self.principal.pk).update(expires_at=timezone.now()+timedelta(days=10))
        self.assertEqual(process_job(run['id'],0),'blocked')
        self.assertEqual(flows.get_run(self.owner,run['id'])['error'],'native_authority_revoked')

    def test_policy_resource_revision_or_identity_change_blocks_the_original_snapshot(self):
        actor=self.actor();grant(self,'run');run=self.launch(actor)
        from backend.maipa.workflows.models import NativeRunAuthority
        evidence=NativeRunAuthority.objects.get(run_id=run['id'])
        PolicyResource.objects.filter(pk=evidence.graph.resource_id).update(revision=2)
        self.assertEqual(process_job(run['id'],0),'blocked')
        self.assertFalse(NodeCheckpoint.objects.exists())

    def test_missing_native_evidence_never_falls_back_to_local_owner_authority(self):
        actor=self.actor();grant(self,'run');run=self.launch(actor)
        from backend.maipa.workflows.models import NativeRunAuthority
        NativeRunAuthority.objects.filter(run_id=run['id']).delete() # Simulated storage damage, no public delete API.
        self.assertEqual(process_job(run['id'],0),'blocked')
        self.assertFalse(NodeCheckpoint.objects.exists())

    def test_native_inspection_requires_current_triage_grant_and_same_original_binding(self):
        actor=self.actor();grant(self,'run');run=self.launch(actor)
        with self.assertRaises(flows.Unavailable):self.api().get_run(actor,run['id'])
        grant(self,'triage');self.assertEqual(self.api().get_run(actor,run['id'])['id'],run['id'])
        AgentPrincipal.objects.filter(pk=self.principal.pk).update(enabled=False)
        with self.assertRaises(flows.Unavailable):self.api().get_run(actor,run['id'])

    def test_no_worker_means_no_native_run_is_acknowledged_or_queued(self):
        actor=self.actor();grant(self,'run');self.api().prepare_graph(self.owner,self.saved['id'],1)
        with self.assertRaises(flows.Unavailable):self.api().dispatch(actor,self.saved['id'],1,{},'offline')
        self.assertEqual(WorkflowRun.objects.count(),0)

    def test_damaged_native_evidence_cannot_be_repaired_by_dispatch_deduplication(self):
        from backend.maipa.workflows.models import NativeRunAuthority
        actor=self.actor();grant(self,'run');self.launch(actor)
        NativeRunAuthority.objects.all().delete()
        with self.assertRaises(flows.Unavailable):self.launch(actor)
        self.assertFalse(NativeRunAuthority.objects.exists())
        self.assertEqual(WorkflowRun.objects.count(),1)

    def test_current_ui_epoch_account_and_role_changes_block_saved_native_authority(self):
        actor=self.actor();grant(self,'run')
        mutations=[(type(self.install),self.install.pk,'access_revision',self.install.access_revision+1),
            (type(self.member),self.member.pk,'role','administrator'),
            (AgentAccount,self.account.pk,'enabled',False)]
        for index,(model,identity,field,value) in enumerate(mutations):
            with self.subTest(field=field):
                run=self.launch(actor,key='changed-ui-'+str(index))
                original=getattr(model.objects.get(pk=identity),field)
                model.objects.filter(pk=identity).update(**{field:value})
                self.assertEqual(process_job(run['id'],0),'blocked')
                self.assertEqual(flows.get_run(self.owner,run['id'])['error'],'native_authority_revoked')
                model.objects.filter(pk=identity).update(**{field:original})
        self.assertFalse(NodeCheckpoint.objects.exists())

    def test_current_human_owner_can_stop_native_work_after_agent_authority_is_revoked(self):
        actor=self.actor();run_grant=grant(self,'run');run=self.launch(actor)
        process_job(run['id'],0);authority.operator_revoke(run_grant.pk,'Synthetic stop')
        with self.assertRaises(flows.Unavailable):flows.cancel(self.other,run['id'],1)
        stopped=flows.cancel(self.owner,run['id'],1)
        self.assertEqual((stopped['status'],process_job(run['id'],2)),('cancelled','stale'))
        self.assertEqual(list(stopped['outputs']),['start'])
