from copy import deepcopy
from pathlib import Path
import tempfile

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from backend.maipa.agent_accounts import form_drafts as drafts, ui
from backend.maipa.agent_accounts.form_tools import FormBroker
from backend.maipa.agent_accounts.models import AgentAccount, SessionReference
from backend.maipa.agent_accounts.references import record_session
from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.storage import prepare_directory, prepare_files
from backend.maipa.workspace.models import Installation, Membership


class SharedFormPathTests(TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='maipa-shared-form-');self.addCleanup(temporary.cleanup)
        data=Path(temporary.name).resolve()/'install';prepare_directory(data);prepare_files(data)
        configured=override_settings(DATA_ROOT=data);configured.enable();self.addCleanup(configured.disable)
        self.install=Installation.objects.create();self.owner=get_user_model().objects.create_user(username='shared-form-owner')
        self.other=get_user_model().objects.create_user(username='shared-form-other')
        for actor in (self.owner,self.other):Membership.objects.create(user=actor,installation=self.install,role='preparer')
        self.account=AgentAccount.objects.create(owner=self.owner,provider='claude',label='Owned synthetic form')
        self.reference=record_session(self.owner,str(self.account.pk),'synthetic-form-private-reference')
        self.lease=ui.open_ui_lease(self.owner,str(self.account.pk),self.reference)
        ui.dispatch_ui(self.owner,str(self.account.pk),self.lease,{'method':'tools/call','params':{
            'name':'maipa_ui_present','arguments':{'revision':0,'title':'Owned client setup','schema':{'id':'client-form','type':'Maipa.ClientCatalog'}}}})
        self.url=f'/api/agent/accounts/{self.account.pk}/ui/{self.lease}/forms/client-form/'
        self.actor={'HTTP_X_MAIPA_AGENT_ACTOR':str(self.owner.pk)};self.client.force_login(self.owner)
        self.broker=FormBroker(self.owner,str(self.account.pk),self.lease)

    def native(self,name,**arguments):return async_to_sync(self.broker.call)(name,arguments)
    def state(self):return drafts.read_new_client_form(self.owner,str(self.account.pk),self.lease,'client-form')
    def change(self,state,values):return drafts.change_new_client_form(self.owner,str(self.account.pk),self.lease,'client-form',{'id':state['id'],'expected_revision':state['revision'],'values':values})
    def post(self,state,values,**headers):
        return self.client.post(self.url,{'id':state['id'],'expected_revision':state['revision'],'values':values},content_type='application/json',secure=True,**(headers or self.actor))

    def test_human_endpoint_reads_and_commits_the_same_private_pending_values(self):
        response=self.client.get(self.url,secure=True,**self.actor);self.assertEqual(response.status_code,200)
        state=response.json();accepted=self.post(state,{'name':'  Human pending  '})
        self.assertEqual(accepted.status_code,200);self.assertEqual(accepted.json(),self.state())
        self.assertEqual(accepted.json()['values']['name'],'  Human pending  ')
        self.assertEqual((Engagement.objects.count(),Entity.objects.count()),(0,0))

    def test_real_public_bridge_reads_and_awaits_a_shared_atomic_commit(self):
        tools=async_to_sync(self.broker.tools)();names={tool['name'] for tool in tools}
        self.assertTrue({'form_list','form_get_values','form_set_value','form_set_values'}.issubset(names))
        self.assertFalse(any(name.startswith('agent_') for name in names))
        listed=self.native('form_list');self.assertFalse(listed.get('isError',False))
        key=listed['structuredContent']['forms'][0]['id'];self.assertEqual(key,self.state()['id'])
        result=self.native('form_set_values',form=key,values={'name':'Native pending','entity_name':'Native entity'})
        self.assertFalse(result.get('isError',False));self.assertEqual(self.state()['values'],{'name':'Native pending','entity_name':'Native entity'})
        self.assertEqual(self.broker.last_activity['action'],'form_set_values')
        self.assertEqual(self.broker.last_activity['form_id'],key)
        self.assertNotIn('Native pending',str(self.broker.last_activity))
        self.assertEqual(self.native('form_get_values',form=key)['structuredContent'],self.state()['values'])
        self.assertEqual(self.client.get(self.url,secure=True,**self.actor).json(),self.state())

    def test_human_edit_after_native_read_refuses_stale_native_write_until_explicit_reread(self):
        self.native('form_get_values');observed=self.state();newer=self.change(observed,{'name':'Newer human input'})
        refused=self.native('form_set_value',field='name',value='Stale native input')
        self.assertTrue(refused['isError']);self.assertEqual(self.state(),newer)
        self.assertIsNone(self.broker.last_activity)
        self.native('form_get_values');accepted=self.native('form_set_value',field='entity_name',value='Native next')
        self.assertFalse(accepted.get('isError',False));self.assertEqual(self.state()['values'],{'name':'Newer human input','entity_name':'Native next'})

    def test_native_cannot_write_without_read_or_submit_business_records(self):
        before=self.state();result=self.native('form_set_value',field='name',value='Unobserved write')
        self.assertTrue(result['isError']);self.assertEqual(self.state(),before)
        self.native('form_get_values');self.assertTrue(self.native('form_submit')['isError'])
        self.assertTrue(self.native('form_focus',field='name')['isError'])
        self.assertEqual((Engagement.objects.count(),Entity.objects.count()),(0,0))

    def test_foreign_actor_revocation_and_removed_component_refuse_without_disclosure(self):
        before=self.state();self.native('form_get_values');self.client.force_login(self.other)
        denied=self.client.get(self.url,secure=True,HTTP_X_MAIPA_AGENT_ACTOR=str(self.other.pk));self.assertEqual(denied.status_code,404)
        self.client.force_login(self.owner);SessionReference.objects.filter(pk=self.reference).update(revoked_at=timezone.now())
        self.assertEqual(self.client.get(self.url,secure=True,**self.actor).status_code,404)
        with self.assertRaises(drafts.FormUnavailable):self.native('form_get_values')
        SessionReference.objects.filter(pk=self.reference).update(revoked_at=None)
        ui.dispatch_ui(self.owner,str(self.account.pk),self.lease,{'method':'tools/call','params':{'name':'maipa_ui_dismiss','arguments':{'revision':1}}})
        self.assertEqual(self.client.get(self.url,secure=True,**self.actor).status_code,404)
        with self.assertRaises(drafts.FormUnavailable):self.native('form_set_value',field='name',value='Closed')
        self.assertNotIn('synthetic-form-private-reference',denied.content.decode())

    def test_http_requires_actor_csrf_current_role_and_closed_bounded_input(self):
        state=self.state();self.assertEqual(self.client.get(self.url,secure=True).status_code,409)
        strict=Client(enforce_csrf_checks=True);strict.force_login(self.owner)
        body={'id':state['id'],'expected_revision':state['revision'],'values':{'name':'CSRF refused'}}
        self.assertEqual(strict.post(self.url,body,content_type='application/json',secure=True,**self.actor).status_code,403)
        for command in ({**body,'actor':str(self.other.pk)}, {'id':state['id'],'expected_revision':True,'values':{}},None):
            self.assertEqual(self.client.post(self.url,command,content_type='application/json',secure=True,**self.actor).status_code,400)
        self.assertEqual(self.client.post(self.url,'x'*65_537,content_type='application/json',secure=True,**self.actor).status_code,413)
        Membership.objects.filter(user=self.owner).update(role='viewer')
        self.assertEqual(self.client.get(self.url,secure=True,**self.actor).status_code,403)

    def test_http_stale_and_native_unknown_or_private_inputs_preserve_pending_values(self):
        self.native('form_get_values');initial=self.state();newer=self.change(initial,{'name':'Current human'})
        response=self.post(initial,{'name':'Stale human'});self.assertEqual(response.status_code,409)
        for name,arguments in [('form_set_value',{'field':'password','value':'unregistered'}),
                               ('form_set_value',{'field':'name','value':'x','actor':str(self.other.pk)}),
                               ('form_get_values',{'form':'foreign-form'})]:
            result=async_to_sync(self.broker.call)(name,arguments);self.assertTrue(result['isError'])
        self.assertEqual(self.state(),newer)
