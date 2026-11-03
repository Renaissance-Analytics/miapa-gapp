"""Settings inspection never becomes provider login or model authority."""
import json,uuid
from unittest.mock import patch
from django.test import TestCase,TransactionTestCase
from backend.maipa.agent_accounts.models import AgentAccount,ProviderProfile,Conversation
from backend.maipa.storage import installation_metadata
from backend.maipa.workspace.models import Membership
from tests import test_ui_composition as composition,test_model_selection as model

class AgentSettingsTests(TestCase):
    setUp=composition.UiCompositionTests.setUp
    def settings(self,actor=None):
        from backend.maipa.agent_accounts.settings_view import snapshot
        return snapshot(actor or self.owner)
    def test_owned_metadata_is_secret_free_and_does_not_prepare_or_connect(self):
        AgentAccount.objects.create(owner=self.other,provider='claude',label='Foreign private account')
        with patch('backend.maipa.agent_accounts.chat.start_worker') as worker:
            value=self.settings()
        self.assertEqual(value['user_id'],str(self.owner.pk));self.assertEqual(value['accounts'],[{'id':str(self.account.pk),'provider':'codex','label':'Synthetic UI account','profile_assigned':False}])
        self.assertEqual({p['id'] for p in value['providers']},{'codex','claude'})
        for forbidden in ('Foreign private account','synthetic-private-provider-handle','home','ready','token'):
            self.assertNotIn(forbidden,json.dumps(value))
        worker.assert_not_called();self.assertFalse(ProviderProfile.objects.exists());self.assertFalse(Conversation.objects.exists())
    def test_original_profile_binding_is_rechecked_and_never_reassigned(self):
        from backend.maipa.agent_accounts.settings_view import SettingsUnavailable
        row=ProviderProfile.objects.create(account=self.account,user=self.owner,installation=self.installation,storage_id=installation_metadata(self.data)['storage_id'],provider='codex')
        self.assertTrue(self.settings()['accounts'][0]['profile_assigned'])
        AgentAccount.objects.filter(pk=self.account.pk).update(owner=self.other)
        with self.assertRaises(SettingsUnavailable):self.settings(self.other)
        AgentAccount.objects.filter(pk=self.account.pk).update(owner=self.owner)
        with patch('backend.maipa.agent_accounts.references.installation_metadata',return_value={'storage_id':str(uuid.uuid4())}):
            with self.assertRaises(SettingsUnavailable):self.settings()
        self.assertEqual(ProviderProfile.objects.get().pk,row.pk)
    def test_current_roles_disabled_accounts_and_unimplemented_registrations_are_honest(self):
        from backend.maipa.agent_accounts.settings_view import SettingsUnavailable
        with self.settings_context(ACP_PROVIDER_LABELS={'independent-acp':'Independent'}):
            value=self.settings();self.assertIn({'id':'independent-acp','label':'Independent','available':False},value['providers'])
        AgentAccount.objects.filter(pk=self.account.pk).update(enabled=False)
        self.assertEqual(self.settings()['accounts'],[])
        Membership.objects.filter(user=self.owner).update(role='viewer')
        with self.assertRaises(SettingsUnavailable):self.settings()
    def settings_context(self,**values):
        from django.test import override_settings
        return override_settings(**values)
    def test_http_requires_original_actor_and_is_read_only_with_no_query_authority(self):
        url='/api/agent/settings/'
        self.assertEqual(self.client.get(url).status_code,401);self.client.force_login(self.owner)
        self.assertEqual(self.client.get(url).status_code,409)
        headers={'HTTP_X_MAIPA_AGENT_ACTOR':str(self.owner.pk)}
        self.assertEqual(self.client.get(url,**headers).status_code,200)
        self.assertEqual(self.client.get(url+'?user_id='+str(self.other.pk),**headers).status_code,400)
        self.assertEqual(self.client.post(url,{'provider':'claude','model_id':'forged'},content_type='application/json',**headers).status_code,405)
    def test_agent_can_present_one_current_settings_component_without_selectors(self):
        api=composition.UiCompositionTests.api(self);lease=api.open_ui_lease(self.owner,str(self.account.pk),self.session)
        call=lambda name,args:api.dispatch_ui(self.owner,str(self.account.pk),lease,{'method':'tools/call','params':{'name':name,'arguments':args}})
        self.assertIn('Maipa.AgentSettings',[item['type'] for item in call('maipa_ui_catalog',{})['components']])
        schema={'id':'agent-settings','type':'Maipa.AgentSettings'}
        self.assertEqual(call('maipa_ui_present',{'revision':0,'title':'Agent settings','schema':schema})['composition']['schema'],schema)
        for invalid in ({**schema,'props':{'user_id':str(self.other.pk)}},{'id':'stack','type':'Maipa.Stack','children':[schema,{**schema,'id':'second'}]}):
            with self.assertRaises(ValueError):api.validate_schema(invalid,'preparer')
        Membership.objects.filter(user=self.owner).update(role='viewer')
        with self.assertRaises(api.UiUnavailable):call('maipa_ui_catalog',{})

class AgentSettingsModelTests(TransactionTestCase):
    setUp=model.ModelSelectionTests.setUp
    def tearDown(self):
        for worker in self.workers:worker.stopping.set()
        for worker in self.workers:
            worker.thread.join(10);self.assertFalse(worker.thread.is_alive())
        self.prepared.stop();self.config.disable();self.temporary.cleanup();super().tearDown()
    state=model.ModelSelectionTests.state
    post=model.ModelSelectionTests.post
    connect=model.ModelSelectionTests.connect
    change=model.ModelSelectionTests.change
    def test_settings_and_actual_model_acknowledgment_preserve_original_conversation(self):
        before=self.connect();metadata=self.client.get('/api/agent/settings/',HTTP_X_MAIPA_AGENT_ACTOR=str(self.owner.pk))
        self.assertEqual(metadata.status_code,200);original=metadata.json()
        after=self.change(before);self.assertEqual(after.status_code,200);after=after.json()
        self.assertEqual(after['models']['currentModelId'],'model-b')
        for key in ('id','messages','ui','connection'):self.assertEqual(after[key],before[key])
        self.assertEqual(self.client.get('/api/agent/settings/',HTTP_X_MAIPA_AGENT_ACTOR=str(self.owner.pk)).json(),original)
        self.assertEqual(self.change(before,'model-a').status_code,409)
