"""A durable model intention never stands in for an actual provider acknowledgment."""
import asyncio,json,threading,time,uuid
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.test import Client,TransactionTestCase
from backend.maipa.acp import AcpClient
from backend.maipa.agent_accounts.models import AgentAccount,Conversation,ProviderProfile,ChatSelection,ChatDraftRecord,SessionReference
from backend.maipa.storage import installation_metadata
from backend.maipa.workspace.models import Installation,Membership
from tests import test_model_selection as base


def close_workers(case):
    for worker in case.workers:worker.stopping.set()
    for worker in case.workers:
        worker.thread.join(10);case.assertFalse(worker.thread.is_alive())
    case.prepared.stop();case.config.disable();case.temporary.cleanup()


class ModelPreferenceTests(TransactionTestCase):
    def tearDown(self):
        close_workers(self);super().tearDown()
    state=base.ModelSelectionTests.state
    post=base.ModelSelectionTests.post
    connect=base.ModelSelectionTests.connect
    change=base.ModelSelectionTests.change

    def setUp(self):
        base.ModelSelectionTests.setUp(self)
        member=Membership.objects.get(user=self.owner)
        self.profile=ProviderProfile.objects.create(account=self.account,user=self.owner,installation=member.installation,
            storage_id=installation_metadata(self.root/'install')['storage_id'],provider='claude')

    def url(self,account=None):return f'/api/agent/accounts/{account or self.account.pk}/model-preference/'
    def read(self,client=None):return (client or self.client).get(self.url(),HTTP_X_MAIPA_AGENT_ACTOR=str(self.owner.pk))
    def save(self,value,client=None):return (client or self.client).post(self.url(),value,content_type='application/json',HTTP_X_MAIPA_AGENT_ACTOR=str(self.owner.pk))
    def remember(self,state,revision=0):return {'mode':'current_model','expected_revision':revision,'selection_revision':state['connection']['revision'],'conversation_revision':state['revision'],'conversation_id':state['id']}
    def models(self):
        from backend.maipa.agent_accounts.models import AccountModelPreference,ModelPreferenceEvent
        return AccountModelPreference.objects,ModelPreferenceEvent.objects
    def chosen(self):
        state=self.connect();response=self.change(state);self.assertEqual(response.status_code,200);return self.state()
    def wait(self,phase):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            value=self.state()
            if value['phase']==phase and (phase!='unavailable' or Conversation.objects.get().worker is None):return value
            time.sleep(.02)
        self.fail('Finite preference peer did not settle')
    def disconnect(self):
        value=self.state();self.assertEqual(self.post({'action':'disconnect','selection_revision':value['connection']['revision']}).status_code,200);self.wait('offline')
    def protect(self):
        return [list(m.objects.values()) for m in (ProviderProfile,ChatSelection,ChatDraftRecord,SessionReference,Installation)]+[
            list(Conversation.objects.values(*[f.attname for f in Conversation._meta.fields if f.attname!='heartbeat']))]

    def test_default_read_is_closed_owned_and_does_not_prepare_a_session(self):
        before=self.protect();response=self.read();self.assertEqual(response.status_code,200)
        self.assertEqual(response.json(),{'user_id':str(self.owner.pk),'account_id':str(self.account.pk),'provider':'claude','revision':0,'model_id':None,'model_name':None,'issue':None})
        self.assertIn('no-store',response.headers['Cache-Control']);self.assertEqual(self.protect(),before)
        response=self.save({'mode':'provider_default','expected_revision':0});self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['revision'],0);self.assertEqual([m.count() for m in self.models()],[0,0])

    def test_remember_derives_the_actual_model_and_audits_without_touching_live_work(self):
        state=self.chosen()
        from backend.maipa.agent_accounts.draft_store import save
        save(self.owner,str(self.account.pk),0,{'v':1,'text':'Retained model draft Ω','pills':[],'attachments':[]},None)
        before=self.protect();response=self.save(self.remember(state));self.assertEqual(response.status_code,200)
        self.assertEqual((response.json()['model_id'],response.json()['model_name'],response.json()['revision']),('model-b','Model B',1))
        self.assertEqual(self.protect(),before);self.assertEqual(self.state()['models'],state['models'])
        preferences,events=self.models();saved=preferences.get();event=events.get()
        self.assertEqual(saved.profile_id,self.profile.pk);self.assertEqual((event.account_id,event.actor_id,event.revision),(self.account.pk,self.owner.pk,1))
        self.assertEqual(event.before,{'model_id':None,'model_name':None});self.assertEqual(event.after,{'model_id':'model-b','model_name':'Model B'})
        self.assertEqual(self.read().json(),response.json())

    def test_current_actor_role_owner_csrf_and_closed_inputs_are_required(self):
        anonymous=Client();self.assertEqual(self.read(anonymous).status_code,401)
        self.assertEqual(self.client.get(self.url(),HTTP_X_MAIPA_AGENT_ACTOR='999').status_code,409)
        self.assertEqual(self.client.get(self.url()+'?actor=999',HTTP_X_MAIPA_AGENT_ACTOR=str(self.owner.pk)).status_code,400)
        member=Membership.objects.get(user=self.owner);other=get_user_model().objects.create_user(username='foreign-model-owner')
        Membership.objects.create(user=other,installation=member.installation,role='preparer');foreign=Client();foreign.force_login(other)
        self.assertEqual(foreign.get(self.url(),HTTP_X_MAIPA_AGENT_ACTOR=str(other.pk)).status_code,404)
        for invalid in ({'mode':'provider_default','expected_revision':True},{'mode':'provider_default','expected_revision':-1},
            {'mode':'provider_default','expected_revision':0,'model_id':'forged'},{'mode':'current_model','expected_revision':0},
            {'mode':'provider_default','expected_revision':0,'user_id':str(other.pk)},'[]','{"mode":"provider_default","mode":"current_model","expected_revision":0}'):
            self.assertEqual(self.save(invalid).status_code,400)
        guarded=Client(enforce_csrf_checks=True);guarded.force_login(self.owner)
        self.assertEqual(self.save({'mode':'provider_default','expected_revision':0},guarded).status_code,403)
        Membership.objects.filter(user=self.owner).update(role='viewer');self.assertEqual(self.read().status_code,403)
        self.assertEqual([m.count() for m in self.models()],[0,0])

    def test_unknown_stale_foreign_session_and_busy_context_cannot_remember_a_model(self):
        state=self.chosen();before=self.protect();other=Client();other.force_login(self.owner)
        self.assertEqual(self.save(self.remember(state),other).status_code,503)
        self.assertEqual(self.save(self.remember(state)|{'conversation_revision':state['revision']-1}).status_code,409)
        self.assertEqual(self.save(self.remember(state)|{'conversation_id':str(uuid.uuid4())}).status_code,409)
        self.assertEqual(self.save(self.remember(state)|{'model_id':'model-b'}).status_code,400)
        self.assertEqual(self.protect(),before)
        Conversation.objects.update(phase='thinking');self.assertEqual(self.save(self.remember(state)).status_code,409)
        Conversation.objects.update(phase='ready');self.assertEqual([m.count() for m in self.models()],[0,0])

    def test_stale_duplicate_and_noop_writes_preserve_acknowledged_preference(self):
        state=self.chosen();first=self.save(self.remember(state));self.assertEqual(first.status_code,200)
        self.assertEqual(self.save(self.remember(state)).status_code,409)
        same=self.save(self.remember(state,1));self.assertEqual(same.status_code,200);self.assertEqual(same.json(),first.json())
        self.assertEqual([m.count() for m in self.models()],[1,1]);before=self.protect()
        default=self.save({'mode':'provider_default','expected_revision':1});self.assertEqual(default.status_code,200)
        self.assertEqual((default.json()['model_id'],default.json()['revision']),(None,2));self.assertEqual(self.protect(),before)
        self.assertEqual(self.state()['models']['currentModelId'],'model-b')

    def test_profile_storage_disabled_account_and_audit_failure_refuse_without_rebinding(self):
        state=self.chosen();before=self.protect()
        from backend.maipa.agent_accounts.models import ModelPreferenceEvent
        with patch.object(ModelPreferenceEvent.objects,'create',side_effect=DatabaseError('synthetic private failure')):
            self.assertEqual(self.save(self.remember(state)).status_code,503)
        self.assertEqual([m.count() for m in self.models()],[0,0]);self.assertEqual(self.protect(),before)
        ProviderProfile.objects.filter(pk=self.profile.pk).update(storage_id=uuid.uuid4());self.assertEqual(self.read().status_code,404)
        ProviderProfile.objects.filter(pk=self.profile.pk).update(storage_id=installation_metadata(self.root/'install')['storage_id'])
        AgentAccount.objects.filter(pk=self.account.pk).update(enabled=False);self.assertEqual(self.read().status_code,404)
        AgentAccount.objects.filter(pk=self.account.pk).update(enabled=True)

    def test_named_preference_applies_after_negotiation_on_explicit_reconnect(self):
        state=self.chosen();self.assertEqual(self.save(self.remember(state)).status_code,200)
        self.disconnect();after=self.connect();self.assertEqual(after['models']['currentModelId'],'model-b')
        self.assertEqual((after['id'],after['connection']['account_id']),(state['id'],str(self.account.pk)))
        self.assertEqual(after['messages'],state['messages']);self.assertEqual(ProviderProfile.objects.get().pk,self.profile.pk)
        self.assertIsNone(self.read().json()['issue']);self.assertEqual([m.count() for m in self.models()],[1,1])

    def test_missing_catalog_retains_intent_and_default_recovery_never_revives_old_model(self):
        state=self.chosen();self.assertEqual(self.save(self.remember(state)).status_code,200);self.disconnect()
        self.flags['synthetic_empty_models']=True;state=self.state()
        self.assertEqual(self.post({'action':'connect','revision':state['revision'],'selection_revision':state['connection']['revision']}).status_code,200)
        failed=self.wait('unavailable');self.assertNotIn('models',failed);self.assertIsNone(failed['ui'])
        self.assertEqual((self.read().json()['model_id'],self.read().json()['issue']),('model-b','missing'))
        self.assertEqual(SessionReference.objects.filter(revoked_at=None).count(),0)
        cleared=self.save({'mode':'provider_default','expected_revision':1});self.assertEqual(cleared.status_code,200)
        self.assertIsNone(cleared.json()['model_id']);self.assertIsNone(cleared.json()['issue']);self.assertEqual(self.state()['phase'],'unavailable')
        self.flags.clear();self.assertEqual(self.connect()['models']['currentModelId'],'model-a')

    def test_native_refusal_never_publishes_ready_or_discards_saved_intent(self):
        state=self.chosen();self.assertEqual(self.save(self.remember(state)).status_code,200);self.disconnect();self.flags['synthetic_model_refusal']=True
        state=self.state();self.assertEqual(self.post({'action':'connect','revision':state['revision'],'selection_revision':state['connection']['revision']}).status_code,200)
        failed=self.wait('unavailable');self.assertNotIn('models',failed);self.assertIsNone(failed['ui'])
        self.assertEqual((self.read().json()['model_id'],self.read().json()['issue']),('model-b','unconfirmed'))

    def test_clearing_preference_during_native_io_fences_the_old_attempt(self):
        state=self.chosen();self.assertEqual(self.save(self.remember(state)).status_code,200);self.disconnect()
        entered,release=threading.Event(),threading.Event();original=AcpClient.set_model
        async def held(client,scope,session,model):
            entered.set();await asyncio.to_thread(release.wait,3);return await original(client,scope,session,model)
        with patch.object(AcpClient,'set_model',held):
            state=self.state();self.assertEqual(self.post({'action':'connect','revision':state['revision'],'selection_revision':state['connection']['revision']}).status_code,200)
            try:
                self.assertTrue(entered.wait(3));cleared=self.save({'mode':'provider_default','expected_revision':1});self.assertEqual(cleared.status_code,200)
            finally:release.set()
            self.wait('unavailable')
        self.assertIsNone(self.read().json()['model_id']);self.assertIsNone(self.read().json()['issue'])
        self.assertEqual(self.connect()['models']['currentModelId'],'model-a')


class ModelPreferenceMigrationTests(TransactionTestCase):
    setUp=ModelPreferenceTests.setUp
    def tearDown(self):
        close_workers(self);super().tearDown()
    def test_downgrade_refuses_before_erasing_original_model_preference(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        from backend.maipa.agent_accounts.models import AccountModelPreference
        member=Membership.objects.get(user=self.owner)
        saved=AccountModelPreference.objects.create(account=self.account,profile=self.profile,user=self.owner,installation=member.installation,
            storage_id=self.profile.storage_id,provider='claude',revision=1,model_id='model-b',model_name='Model B')
        before=list(AccountModelPreference.objects.values())
        try:
            with self.assertRaisesRegex(RuntimeError,'Saved model preferences require offline backup recovery'):
                MigrationExecutor(connection).migrate([('agent_accounts','0011_account_metadata')])
            self.assertIn(('agent_accounts','0012_model_preferences'),MigrationExecutor(connection).loader.applied_migrations)
        finally:
            executor=MigrationExecutor(connection);executor.migrate(executor.loader.graph.leaf_nodes())
        self.assertEqual(list(AccountModelPreference.objects.values()),before);self.assertEqual(AccountModelPreference.objects.get().pk,saved.pk)
