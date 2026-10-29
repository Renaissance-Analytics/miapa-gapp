"""Expected shared human/native outcomes, with real DB authority and no provider call."""
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.test import TestCase, override_settings

from backend.maipa.agent_accounts.models import AgentAccount, SessionReference
from backend.maipa.agent_accounts.references import record_session
from backend.maipa.agent_accounts import ui
from backend.maipa.storage import prepare_directory, prepare_files
from backend.maipa.workspace.models import Installation, Membership, PersonalViews

NOTE={'id':'shared-note','type':'Maipa.Note','props':{'text':'Synthetic saved commentary'}}
URL='/api/workspace/views/'


class PersonalViewToolsTests(TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='maipa-view-tools-');self.addCleanup(temporary.cleanup)
        self.data=Path(temporary.name).resolve()/'install';prepare_directory(self.data);prepare_files(self.data)
        configured=override_settings(DATA_ROOT=self.data);configured.enable();self.addCleanup(configured.disable)
        self.install=Installation.objects.create()
        self.owner=get_user_model().objects.create_user(username='pin-owner')
        self.other=get_user_model().objects.create_user(username='pin-other')
        for actor in (self.owner,self.other):Membership.objects.create(user=actor,installation=self.install,role='preparer')
        self.account=AgentAccount.objects.create(owner=self.owner,provider='claude',label='Synthetic pin account')
        self.session=record_session(self.owner,str(self.account.pk),'synthetic-private-native-reference')
        self.lease=ui.open_ui_lease(self.owner,str(self.account.pk),self.session)
        self.client.force_login(self.owner)

    def call(self,name,args,actor=None,lease=None):
        return ui.dispatch_ui(actor or self.owner,str(self.account.pk),lease or self.lease,
            {'method':'tools/call','params':{'name':name,'arguments':args}})

    def state(self):
        response=self.client.get(URL,secure=True);self.assertEqual(response.status_code,200);return response.json()

    def save(self,state,**extra):
        return self.call('maipa_views_save',{'views_id':state['views_id'],'expected_revision':state['revision'],
            'title':'Shared review','schema':NOTE,**extra})

    def test_native_save_human_rename_and_native_update_share_one_owned_history(self):
        initial=self.state();self.assertEqual(self.call('maipa_views_list',{}),initial)
        saved=self.save(initial);self.assertEqual(saved,self.state());key=saved['views'][0]['id']
        human=self.client.post(URL,{'action':'rename','expected_revision':saved['revision'],'id':key,'title':'Human title'},
            content_type='application/json',secure=True,HTTP_X_MAIPA_VIEWS=initial['views_id'])
        self.assertEqual(human.status_code,200);current=human.json()
        updated=self.call('maipa_views_change',{'views_id':current['views_id'],'expected_revision':current['revision'],
            'action':'update','id':key,'title':'Shared latest','schema':{**NOTE,'props':{'text':'Verified shared revision'}}})
        self.assertEqual(updated,self.state());view=updated['views'][0]
        self.assertEqual((view['id'],view['schema']['id'],view['revision']),(key,NOTE['id'],3))
        self.assertEqual([r['title'] for r in view['history']],['Shared review','Human title'])
        self.assertNotIn('synthetic-private-native-reference',json.dumps(updated))

    def test_stale_native_write_after_human_edit_has_no_partial_commit(self):
        saved=self.save(self.state());key=saved['views'][0]['id']
        winner=self.client.post(URL,{'action':'unpin','id':key,'expected_revision':saved['revision']},
            content_type='application/json',secure=True,HTTP_X_MAIPA_VIEWS=saved['views_id'])
        self.assertEqual(winner.status_code,200)
        with self.assertRaises(ui.UiConflict):self.save(saved)
        self.assertEqual(self.state(),winner.json())

    def test_open_requires_both_current_definition_and_ui_revisions(self):
        saved=self.save(self.state());key=saved['views'][0]['id']
        args={'views_id':saved['views_id'],'expected_revision':saved['revision'],'id':key,'revision':0}
        opened=self.call('maipa_views_open',args)
        self.assertEqual(opened,{'id':self.lease,'revision':1,'composition':{'title':'Shared review','schema':NOTE}})
        with self.assertRaises(ui.UiConflict):self.call('maipa_views_open',args)
        current=self.call('maipa_views_change',{'views_id':saved['views_id'],'expected_revision':saved['revision'],
            'action':'rename','id':key,'title':'Current title'})
        with self.assertRaises(ui.UiConflict):self.call('maipa_views_open',{**args,'revision':1})
        self.assertEqual(ui.ui_snapshot(self.owner,str(self.account.pk),self.lease),opened)
        self.assertEqual(self.call('maipa_views_open',{**args,'expected_revision':current['revision'],'revision':1})['composition']['title'],'Current title')

    def test_old_or_foreign_native_authority_cannot_reopen_or_save_owned_pins(self):
        saved=self.save(self.state());key=saved['views'][0]['id']
        with self.assertRaises(ui.UiUnavailable):self.call('maipa_views_list',{},actor=self.other)
        SessionReference.objects.filter(pk=self.session).update(revoked_at='2026-10-29T00:00:00Z')
        with self.assertRaises(ui.UiUnavailable):self.call('maipa_views_list',{})
        self.assertEqual(self.state(),saved)
        fresh=record_session(self.owner,str(self.account.pk),'fresh-private-reference')
        lease=ui.open_ui_lease(self.owner,str(self.account.pk),fresh)
        opened=self.call('maipa_views_open',{'views_id':saved['views_id'],'expected_revision':saved['revision'],'id':key,'revision':0},lease=lease)
        self.assertEqual((opened['id'],opened['revision'],opened['composition']['schema']),(lease,1,NOTE))

    def test_demotion_never_restores_saved_admin_components_through_a_fresh_lease(self):
        Membership.objects.filter(user=self.owner).update(role='administrator')
        admin=ui.open_ui_lease(self.owner,str(self.account.pk),self.session)
        saved=self.call('maipa_views_save',{'views_id':self.state()['views_id'],'expected_revision':0,
            'title':'Restricted','schema':{'id':'users','type':'Maipa.AdminUsers'}},lease=admin)
        Membership.objects.filter(user=self.owner).update(role='preparer')
        fresh=ui.open_ui_lease(self.owner,str(self.account.pk),self.session)
        current=self.call('maipa_views_list',{},lease=fresh)
        self.assertIsNone(current['views'][0]['schema']);self.assertFalse(current['views'][0]['available'])
        with self.assertRaises(ui.UiUnavailable):self.call('maipa_views_open',{'views_id':saved['views_id'],
            'expected_revision':saved['revision'],'id':saved['views'][0]['id'],'revision':0},lease=fresh)
        self.assertIsNone(ui.ui_snapshot(self.owner,str(self.account.pk),fresh)['composition'])

    def test_native_failure_rolls_back_revision_and_cannot_claim_success(self):
        initial=self.state()
        with patch.object(PersonalViews,'save',side_effect=DatabaseError('synthetic private data')):
            with self.assertRaises(ui.UiUnavailable) as caught:self.save(initial)
        self.assertNotIn('synthetic private data',str(caught.exception));self.assertEqual(self.state(),initial)

    def test_closed_native_arguments_cannot_choose_other_authority_or_open_history(self):
        initial=self.state()
        for name,args in [('maipa_views_list',{'user_id':str(self.other.pk)}),
                ('maipa_views_save',{'views_id':initial['views_id'],'expected_revision':0,'title':'Unsafe','schema':NOTE,'user_id':str(self.other.pk)}),
                ('maipa_views_open',{'views_id':initial['views_id'],'expected_revision':0,'id':'invalid','revision':0,'history_revision':1})]:
            with self.subTest(name=name):
                with self.assertRaises(ui.UiUnavailable):self.call(name,args)
        self.assertEqual(self.state(),initial)

    def test_explicit_bridge_identity_is_bounded_and_duplicate_create_cannot_replace(self):
        first=self.state();key='12345678-1234-4234-8234-123456789012'
        saved=self.save(first,id=key);self.assertEqual(saved['views'][0]['id'],key)
        with self.assertRaises(ui.UiConflict):self.save(saved,id=key)
        with self.assertRaises(ui.UiUnavailable):self.save(saved,id='arbitrary-path')
        self.assertEqual(self.state(),saved)

    def test_visible_definition_update_and_ui_revision_commit_or_roll_back_together(self):
        saved=self.save(self.state());key=saved['views'][0]['id']
        self.call('maipa_views_open',{'views_id':saved['views_id'],'expected_revision':saved['revision'],'id':key,'revision':0})
        args={'views_id':saved['views_id'],'expected_revision':saved['revision'],'action':'rename','id':key,'title':'Atomic title','revision':0}
        with self.assertRaises(ui.UiConflict):self.call('maipa_views_change',args)
        self.assertEqual(self.state(),saved)
        accepted=self.call('maipa_views_change',{**args,'revision':1})
        self.assertEqual(accepted['views'],self.state())
        self.assertEqual(accepted['ui']['composition']['title'],'Atomic title')
        self.assertEqual(accepted['ui']['revision'],2)
        self.assertEqual(ui.ui_snapshot(self.owner,str(self.account.pk),self.lease),accepted['ui'])

    def test_aggregate_definitions_are_bounded_before_the_native_reply_budget(self):
        current=self.state()
        large={**NOTE,'props':{'text':'x'*2000}}
        for number in range(3):
            current=self.call('maipa_views_save',{'views_id':current['views_id'],'expected_revision':current['revision'],
                'title':f'Large {number}','schema':large})
        before=current
        for number in range(12):
            try:
                current=self.call('maipa_views_change',{'views_id':current['views_id'],'expected_revision':current['revision'],
                    'action':'update','id':current['views'][number%3]['id'],'title':'Large','schema':large})
            except ui.UiConflict:
                self.assertEqual(self.state(),before);break
            before=current
        else:self.fail('Unbounded saved definitions would overflow the native reply budget')
        self.assertLess(len(json.dumps(current)),25_000)
