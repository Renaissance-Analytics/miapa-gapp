"""Independent A07 private component definitions; no live provider or client data."""
import json
from concurrent.futures import ThreadPoolExecutor
import threading
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connections, DatabaseError
from django.test import Client, TestCase, TransactionTestCase

from backend.maipa.workspace.models import Installation, Membership

URL='/api/workspace/views/'
NOTE={'id':'review-note','type':'Maipa.Note','props':{'text':'Unverified synthetic commentary'}}


class PersonalViewsTests(TestCase):
    def setUp(self):
        self.installation=Installation.objects.create()
        self.owner=get_user_model().objects.create_user(username='view-owner')
        self.other=get_user_model().objects.create_user(username='view-other')
        for user in (self.owner,self.other):
            Membership.objects.create(user=user,installation=self.installation,role='preparer')
        self.client.force_login(self.owner)

    def state(self,client=None):
        response=(client or self.client).get(URL,secure=True)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response['Cache-Control'],'no-store')
        return response.json()

    def change(self,previous,action='create',client=None,**fields):
        return (client or self.client).post(URL,{'action':action,'expected_revision':previous['revision'],**fields},
            content_type='application/json',secure=True,HTTP_X_MAIPA_VIEWS=previous['views_id'])

    def create(self,previous,**fields):
        return self.change(previous,title='Review note',schema=NOTE,**fields)

    def test_definition_and_identity_survive_a_new_login_without_changing_chat_selection(self):
        initial=self.state()
        self.assertEqual(set(initial),{'views_id','user_id','revision','views'})
        self.assertEqual((initial['user_id'],initial['revision'],initial['views']),(str(self.owner.pk),0,[]))
        chat=self.client.get('/api/agent/chat/',secure=True,HTTP_X_MAIPA_AGENT_ACTOR=str(self.owner.pk)).json()
        saved=self.create(initial);self.assertEqual(saved.status_code,200)
        snapshot=saved.json();self.assertEqual(snapshot['views_id'],initial['views_id'])
        self.assertEqual(snapshot['revision'],1);self.assertEqual(len(snapshot['views']),1)
        view=snapshot['views'][0];self.assertRegex(view['id'],r'^[0-9a-f-]{36}$')
        self.assertEqual((view['title'],view['schema'],view['revision']),('Review note',NOTE,1))
        self.client.logout();other_login=Client();other_login.force_login(self.owner)
        self.assertEqual(self.state(other_login),snapshot)
        self.assertEqual(other_login.get('/api/agent/chat/',secure=True,HTTP_X_MAIPA_AGENT_ACTOR=str(self.owner.pk)).json(),chat)

    def test_stale_save_or_rename_never_overwrites_the_current_definition(self):
        first=self.state();saved=self.create(first);self.assertEqual(saved.status_code,200)
        current=saved.json();identity=current['views'][0]['id']
        self.assertEqual(self.create(first).status_code,409)
        winner=self.change(current,'rename',id=identity,title='Updated title')
        self.assertEqual(winner.status_code,200);winner=winner.json()
        self.assertEqual(winner['revision'],2)
        self.assertEqual(winner['views'][0]['title'],'Updated title')
        self.assertEqual(winner['views'][0]['schema'],NOTE)
        self.assertEqual(self.change(current,'rename',id=identity,title='Stale title').status_code,409)
        self.assertEqual(self.state(),winner)

    def test_foreign_login_and_authority_fields_cannot_adopt_or_replace_definitions(self):
        own=self.state();saved=self.create(own);self.assertEqual(saved.status_code,200);own=saved.json()
        foreign=Client();foreign.force_login(self.other);other=self.state(foreign)
        self.assertNotEqual(other['views_id'],own['views_id']);self.assertEqual(other['views'],[])
        self.assertEqual(self.change(own,'rename',client=foreign,id=own['views'][0]['id'],title='Foreign').status_code,409)
        self.assertEqual(self.change(other,'rename',client=foreign,id=own['views'][0]['id'],title='Foreign').status_code,404)
        self.assertEqual(self.create(own,user_id=str(self.other.pk)).status_code,400)
        self.assertEqual(self.state(foreign),other);self.assertEqual(self.state(),own)

    def test_invalid_components_unsafe_props_and_duplicate_handles_are_rejected_before_storage(self):
        first=self.state()
        for schema in (
            {'id':'bad','type':'Executable','props':{'code':'synthetic-invalid'}},
            {'id':'bad','type':'Maipa.Note','props':{'text':'safe','html':'<script>synthetic</script>'}},
            {'id':'group','type':'Maipa.Stack','children':[NOTE,NOTE]},
            {'id':'bad','type':'Maipa.ClientFacts','props':{'user_id':str(self.other.pk)}},
            {'id':'bad','type':'Maipa.AdminUsers'},
        ):
            with self.subTest(schema=schema):
                response=self.change(first,title='Unsafe',schema=schema)
                self.assertEqual(response.status_code,400)
        self.assertEqual(self.state(),first)

    def test_live_role_revocation_cannot_reuse_personal_view_authority(self):
        first=self.state();self.assertEqual(self.create(first).status_code,200)
        Membership.objects.filter(user=self.owner).update(role='viewer')
        self.assertEqual(self.client.get(URL,secure=True).status_code,403)
        self.assertEqual(self.create(first).status_code,403)
        Membership.objects.filter(user=self.owner).update(role='preparer',enabled=False)
        self.assertEqual(self.client.get(URL,secure=True).status_code,403)
        Membership.objects.filter(user=self.owner).update(enabled=True)
        self.assertEqual(len(self.state()['views']),1)

    def test_authentication_csrf_origin_and_methods_are_enforced_before_mutation(self):
        self.assertEqual(Client().get(URL,secure=True).status_code,401)
        first=self.state();protected=Client(enforce_csrf_checks=True);protected.force_login(self.owner)
        body={'action':'create','expected_revision':0,'title':'Review','schema':NOTE}
        headers={'HTTP_X_MAIPA_VIEWS':first['views_id']}
        self.assertEqual(protected.post(URL,body,content_type='application/json',secure=True,**headers).status_code,403)
        token=protected.get('/api/session/csrf/',secure=True).json()['csrf_token']
        self.assertEqual(protected.post(URL,body,content_type='application/json',secure=True,**headers,
            HTTP_X_CSRFTOKEN=token,HTTP_ORIGIN='https://foreign.invalid').status_code,403)
        self.assertEqual(self.state(),first)
        response=self.client.put(URL,'{}',content_type='application/json',secure=True)
        self.assertEqual((response.status_code,response['Allow']),(405,'GET, POST'))

    def test_definition_updates_preserve_stable_handles_and_only_bounded_prior_revisions(self):
        saved=self.create(self.state());self.assertEqual(saved.status_code,200);current=saved.json()
        identity=current['views'][0]['id']
        for number in range(1,7):
            schema={'id':NOTE['id'],'type':'Maipa.Note','props':{'text':f'Synthetic revision {number}'}}
            response=self.change(current,'update',id=identity,title='Review note',schema=schema)
            self.assertEqual(response.status_code,200);current=response.json()
        view=current['views'][0]
        self.assertEqual((view['id'],view['revision'],view['schema']['id']),(identity,7,NOTE['id']))
        self.assertEqual([entry['revision'] for entry in view['history']],[3,4,5,6])
        self.assertEqual(view['history'][0]['schema']['props']['text'],'Synthetic revision 2')
        self.assertEqual(view['schema']['props']['text'],'Synthetic revision 6')
        self.assertEqual(self.change(current,'update',id=identity,title='Bad',schema={**NOTE,'id':'invalid handle'}).status_code,400)
        self.assertEqual(self.state(),current)

    def test_pin_order_and_unpin_preserve_the_saved_definition_and_reject_partial_reorders(self):
        first=self.create(self.state());self.assertEqual(first.status_code,200)
        second=self.create(first.json());self.assertEqual(second.status_code,200);current=second.json()
        a,b=[view['id'] for view in current['views']]
        ordered=self.change(current,'reorder',ids=[b,a]);self.assertEqual(ordered.status_code,200);current=ordered.json()
        self.assertEqual([view['id'] for view in current['views']],[b,a])
        for invalid in ([a],[a,a],['12345678-1234-4234-8234-123456789012',a]):
            self.assertEqual(self.change(current,'reorder',ids=invalid).status_code,400)
        self.assertEqual(self.state(),current)
        unpinned=self.change(current,'unpin',id=b);self.assertEqual(unpinned.status_code,200);current=unpinned.json()
        self.assertEqual(len(current['views']),2)
        self.assertEqual((current['views'][0]['id'],current['views'][0]['pinned'],current['views'][0]['schema']),(b,False,NOTE))
        self.assertEqual(current['views'][1]['pinned'],True)
        repinned=self.change(current,'pin',id=b);self.assertEqual(repinned.status_code,200)
        self.assertTrue(repinned.json()['views'][0]['pinned'])

    def test_demotion_redacts_restricted_components_and_cannot_restore_prior_admin_authority(self):
        first=self.state();Membership.objects.filter(user=self.owner).update(role='administrator')
        saved=self.change(first,title='Access controls',schema={'id':'local-users','type':'Maipa.AdminUsers'})
        self.assertEqual(saved.status_code,200);self.assertTrue(saved.json()['views'][0]['available'])
        Membership.objects.filter(user=self.owner).update(role='preparer')
        current=self.state();restricted=current['views'][0]
        self.assertFalse(restricted['available']);self.assertIsNone(restricted['schema'])
        self.assertEqual(self.change(current,'update',id=restricted['id'],title='Denied',schema={'id':'local-users','type':'Maipa.AdminUsers'}).status_code,400)
        safe=self.change(current,'update',id=restricted['id'],title='Safe note',schema=NOTE)
        self.assertEqual(safe.status_code,200);view=safe.json()['views'][0]
        self.assertTrue(view['available']);self.assertEqual(view['schema'],NOTE)
        self.assertFalse(view['history'][0]['available']);self.assertIsNone(view['history'][0]['schema'])

    def test_strict_json_revision_and_collection_limits_never_partially_save(self):
        first=self.state()
        for revision in (True,-1,'0',None,2_000_000_000):
            self.assertEqual(self.create({**first,'revision':revision}).status_code,400)
        for body,status in (('[]',400),('null',400),('{"action":"create","action":"rename"}',400),
                ('{"expected_revision":NaN}',400),(' '*65_537,413)):
            response=self.client.post(URL,body,content_type='application/json',secure=True,HTTP_X_MAIPA_VIEWS=first['views_id'])
            self.assertEqual(response.status_code,status)
        self.assertEqual(self.state(),first)
        current=first
        for _ in range(8):
            saved=self.create(current);self.assertEqual(saved.status_code,200);current=saved.json()
        self.assertEqual(self.create(current).status_code,409)
        self.assertEqual(self.state(),current)

    def test_identity_guard_and_misbound_storage_never_reinitialize_an_existing_collection(self):
        first=self.state();body={'action':'create','expected_revision':0,'title':'Review','schema':NOTE}
        from backend.maipa.workspace.models import PersonalViews
        for headers in ({},{'HTTP_X_MAIPA_VIEWS':'invalid'},
                {'HTTP_X_MAIPA_VIEWS':'12345678-1234-4234-8234-123456789012'}):
            self.assertEqual(self.client.post(URL,body,content_type='application/json',secure=True,**headers).status_code,409)
        self.assertEqual(self.state(),first)
        PersonalViews.objects.filter(user=self.owner).update(installation_id=999999)
        self.assertEqual(self.client.get(URL,secure=True).status_code,403)
        self.assertEqual(self.create(first).status_code,409)
        PersonalViews.objects.filter(user=self.owner).update(installation=self.installation)
        self.assertEqual(self.state(),first)

    def test_storage_failure_rolls_back_the_reserved_revision_without_claiming_a_saved_view(self):
        first=self.state()
        from backend.maipa.workspace.models import PersonalViews
        with patch.object(PersonalViews,'save',side_effect=DatabaseError('synthetic private diagnostic')):
            response=self.create(first)
        self.assertEqual(response.status_code,503)
        self.assertNotIn('synthetic private diagnostic',response.content.decode())
        self.assertEqual(self.state(),first)


class ConcurrentPersonalViewsTests(TransactionTestCase):
    def test_concurrent_real_connections_cannot_both_save_the_same_collection_revision(self):
        installation=Installation.objects.create();owner=get_user_model().objects.create_user(username='views-race')
        Membership.objects.create(user=owner,installation=installation,role='preparer')
        clients=[Client(),Client()]
        for client in clients:client.force_login(owner)
        initial=clients[0].get(URL,secure=True);self.assertEqual(initial.status_code,200);initial=initial.json()
        barrier=threading.Barrier(2)
        def attempt(number):
            try:
                barrier.wait(timeout=5)
                return clients[number].post(URL,{'action':'create','expected_revision':0,
                    'title':f'Synthetic winner {number}','schema':NOTE},content_type='application/json',secure=True,
                    HTTP_X_MAIPA_VIEWS=initial['views_id']).status_code
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:statuses=list(pool.map(attempt,(0,1)))
        self.assertEqual(statuses.count(200),1)
        self.assertTrue(all(status in (200,409,503) for status in statuses))
        current=clients[0].get(URL,secure=True);self.assertEqual(current.status_code,200);current=current.json()
        self.assertEqual((current['revision'],len(current['views'])),(1,1))
        self.assertEqual(current['views'][0]['title'],f'Synthetic winner {statuses.index(200)}')
