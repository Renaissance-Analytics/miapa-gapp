"""P06 actual private client administration and shared metadata write boundary."""
import json
import uuid
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.test import Client, TestCase

from backend.maipa.intake import services
from backend.maipa.intake.models import Conclusion, Engagement, Entity, FactRevision
from backend.maipa.workspace.models import Installation, Membership

URL = '/api/intake/catalog/'


class ClientCatalogTests(TestCase):
    def setUp(self):
        self.install = Installation.objects.create()
        self.owner = get_user_model().objects.create_user(username='catalog-owner')
        self.other = get_user_model().objects.create_user(username='catalog-other')
        self.member = Membership.objects.create(installation=self.install, user=self.owner, role='preparer')
        Membership.objects.create(installation=self.install, user=self.other, role='administrator')
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.owner)

    def read(self, selected=None):
        response = self.client.get(URL + (f'?engagement_id={selected}' if selected else ''), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'no-store')
        return response.json()

    def post(self, snapshot, action, *, token=True, actor=None, origin='https://testserver', **fields):
        body = dict(identity=snapshot['identity'], installation_id=snapshot['installation_id'],
                    expected_revision=snapshot['revision'], action=action, **fields)
        headers = {'HTTP_ORIGIN': origin, 'HTTP_X_MAIPA_CLIENT_ACTOR': str(actor or self.owner.pk)}
        if token:
            headers['HTTP_X_CSRFTOKEN'] = self.client.get('/api/session/csrf/', secure=True).json()['csrf_token']
        return self.client.post(URL, data=json.dumps(body), content_type='application/json', secure=True, **headers)

    def create(self, snapshot=None, name='Synthetic engagement', entity_name='Synthetic entity'):
        previous = snapshot or self.read()
        response = self.post(previous, 'create_engagement', name=name, entity_name=entity_name)
        self.assertEqual(response.status_code, 201)
        return response.json()

    def test_empty_catalog_is_actor_bound_and_lists_only_current_owners_metadata(self):
        empty = self.read()
        self.assertEqual(set(empty), {'actor_id', 'identity', 'installation_id', 'revision',
            'engagements', 'selected_engagement_id', 'entities', 'events'})
        self.assertEqual(empty['actor_id'], str(self.owner.pk))
        self.assertEqual(empty['installation_id'], str(self.install.pk))
        self.assertEqual((empty['engagements'], empty['entities'], empty['selected_engagement_id']), ([], [], None))
        self.assertEqual(empty['revision'], 0)
        own = services.create_engagement(self.owner, {'name': 'Owned display', 'entity_name': 'Owned entity'})
        foreign = services.create_engagement(self.other, {'name': 'Private other display', 'entity_name': 'Private other entity'})
        value = self.read()
        self.assertEqual(value['engagements'], [{'id': str(own.pk), 'name': own.name, 'entity_count': 1}])
        self.assertEqual(value['selected_engagement_id'], str(own.pk))
        self.assertEqual(value['entities'], [{'id': str(own.entities.get().pk), 'name': 'Owned entity'}])
        self.assertNotIn(str(foreign.pk), json.dumps(value))
        self.assertNotIn('Private other', json.dumps(value))

    def test_create_add_and_rename_have_exact_next_revision_receipts_and_real_audit(self):
        start = self.read()
        created = self.create(start)
        self.assertEqual(set(created), {'status', 'actor_id', 'identity', 'installation_id', 'revision',
            'action', 'target_id', 'engagement_id'})
        self.assertEqual((created['status'], created['actor_id'], created['identity'], created['installation_id'], created['revision']),
            ('saved', str(self.owner.pk), start['identity'], str(self.install.pk), start['revision']+1))
        group = Engagement.objects.get(pk=created['target_id'])
        self.assertEqual(created['engagement_id'], str(group.pk))
        self.assertEqual(group.owner_id, self.owner.pk)
        snapshot = self.read(str(group.pk))
        add = self.post(snapshot, 'create_entity', engagement_id=str(group.pk), name='Second entity')
        self.assertEqual(add.status_code, 201)
        entity = Entity.objects.get(pk=add.json()['target_id'])
        renamed = self.post(self.read(str(group.pk)), 'rename_entity', entity_id=str(entity.pk), name='Renamed entity')
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(renamed.json()['revision'], add.json()['revision']+1)
        last = self.post(self.read(str(group.pk)), 'rename_engagement', engagement_id=str(group.pk), name='Renamed engagement')
        self.assertEqual(last.status_code, 200)
        group.refresh_from_db(); entity.refresh_from_db()
        self.assertEqual((group.name, entity.name, entity.revision), ('Renamed engagement', 'Renamed entity', 0))
        self.assertEqual(FactRevision.objects.count(), 0)
        events = self.read(str(group.pk))['events']
        self.assertEqual([item['action'] for item in events], ['rename_engagement', 'rename_entity', 'create_entity', 'create_engagement'])
        self.assertEqual([item['revision'] for item in events], [4, 3, 2, 1])
        self.assertTrue(all(set(item) == {'action', 'target_id', 'engagement_id', 'revision', 'at'} for item in events))
        self.assertNotIn('Renamed', json.dumps(events))

    def test_duplicate_command_and_real_competing_tab_write_cannot_create_twice(self):
        original = self.read()
        first = self.create(original)
        response = self.post(original, 'create_engagement', name='Do not duplicate', entity_name='Do not duplicate')
        self.assertEqual((response.status_code, response.json()), (409, {'error': {'code': 'revision_conflict'}}))
        self.assertEqual(Engagement.objects.count(), 1)
        self.assertEqual(self.read()['revision'], first['revision'])
        current = self.read()
        sibling = services.create_entity(self.owner, first['engagement_id'], {'name': 'Other real writer'})
        stale = self.post(current, 'rename_engagement', engagement_id=first['engagement_id'], name='Stale name')
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(Engagement.objects.get().name, 'Synthetic engagement')
        self.assertEqual(sibling.name, 'Other real writer')

    def test_existing_intake_creation_shares_catalog_revision_and_audit(self):
        initial = self.read()
        group = services.create_engagement(self.owner, {'name': 'Existing service', 'entity_name': 'Initial service entity'})
        first = self.read(str(group.pk))
        self.assertEqual(first['revision'], initial['revision']+1)
        services.create_entity(self.owner, group.pk, {'name': 'Second service entity'})
        second = self.read(str(group.pk))
        self.assertEqual(second['revision'], first['revision']+1)
        self.assertEqual([event['action'] for event in second['events']], ['create_entity', 'create_engagement'])
        response = self.client.get('/api/intake/engagements/', secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {'engagements'})
        self.assertEqual(set(response.json()['engagements'][0]), {'id', 'name', 'entities'})

    def test_missing_csrf_and_foreign_origin_do_not_write_catalog_or_events(self):
        initial = self.read()
        for options in ({'token': False}, {'origin': 'https://foreign.invalid'}):
            response = self.post(initial, 'create_engagement', name='Private', entity_name='Private', **options)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json(), {'error': {'code': 'csrf_failed'}})
        self.assertEqual(self.read(), initial)
        self.assertEqual(Engagement.objects.count(), 0)

    def test_restricted_disabled_and_signed_out_principals_cannot_read_or_mutate(self):
        initial = self.read()
        for change, restore in (({'role': 'viewer'}, {'role': 'preparer'}),
                                ({'role': 'reviewer'}, {'role': 'preparer'}),
                                ({'enabled': False}, {'enabled': True})):
            Membership.objects.filter(pk=self.member.pk).update(**change)
            self.assertEqual(self.client.get(URL, secure=True).status_code, 403)
            self.assertEqual(self.post(initial, 'create_engagement', name='No', entity_name='No').status_code, 403)
            Membership.objects.filter(pk=self.member.pk).update(**restore)
        get_user_model().objects.filter(pk=self.owner.pk).update(is_active=False)
        self.assertIn(self.client.get(URL, secure=True).status_code, (401, 403))
        get_user_model().objects.filter(pk=self.owner.pk).update(is_active=True)
        self.client.logout()
        self.assertEqual(self.client.get(URL, secure=True).status_code, 401)
        self.assertEqual(Engagement.objects.count(), 0)

    def test_original_actor_identity_installation_and_foreign_targets_are_independently_bound(self):
        initial = self.read()
        self.assertEqual(self.post(initial, 'create_engagement', actor=self.other.pk, name='No', entity_name='No').status_code, 409)
        for field in ('identity', 'installation_id'):
            stale = {**initial, field: str(uuid.uuid4())}
            self.assertEqual(self.post(stale, 'create_engagement', name='No', entity_name='No').status_code, 409)
        foreign = services.create_engagement(self.other, {'name': 'Other private engagement', 'entity_name': 'Other private entity'})
        for action, target in [('create_entity', {'engagement_id': str(foreign.pk)}),
                              ('rename_engagement', {'engagement_id': str(foreign.pk)}),
                              ('rename_entity', {'entity_id': str(foreign.entities.get().pk)})]:
            response = self.post(initial, action, name='Do not disclose', **target)
            self.assertEqual(response.status_code, 404)
            self.assertNotIn(b'Other private', response.content)
        foreign.refresh_from_db()
        self.assertEqual(foreign.name, 'Other private engagement')
        self.assertEqual(self.read()['revision'], initial['revision'])

    def test_strict_commands_refuse_identity_path_credential_fields_and_invalid_names(self):
        initial = self.read()
        for invalid in ({'name': ''}, {'name': 'x'*129}, {'name': 'bad\x00name'}, {'name': 1},
                        {'owner_id': str(self.other.pk)}, {'home': 'C:/private'}, {'password': 'never-log'},
                        {'command': 'never-run'}, {'expected_revision': True}, {'expected_revision': -1},
                        {'action': 'delete_all'}, {'extra': []}):
            body = {'identity': initial['identity'], 'installation_id': initial['installation_id'],
                'expected_revision': initial['revision'], 'action': 'create_engagement',
                'name': 'Valid engagement', 'entity_name': 'Valid entity', **invalid}
            token = self.client.get('/api/session/csrf/', secure=True).json()['csrf_token']
            response = self.client.post(URL, data=json.dumps(body), content_type='application/json', secure=True,
                HTTP_ORIGIN='https://testserver', HTTP_X_CSRFTOKEN=token, HTTP_X_MAIPA_CLIENT_ACTOR=str(self.owner.pk))
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json(), {'error': {'code': 'invalid_request'}})
        self.assertEqual(Engagement.objects.count(), 0)
        self.assertEqual(self.read()['revision'], 0)
        refused = self.client.delete(URL, secure=True,
            HTTP_X_CSRFTOKEN=self.client.get('/api/session/csrf/', secure=True).json()['csrf_token'],
            HTTP_ORIGIN='https://testserver')
        self.assertEqual(refused.status_code, 405)
        self.assertEqual(refused['Allow'], 'GET, POST')

    def test_duplicate_key_malformed_and_overbudget_requests_have_safe_errors(self):
        self.read()
        token = self.client.get('/api/session/csrf/', secure=True).json()['csrf_token']
        for raw, expected in (('{"action":"a","action":"b"}', 400), ('null', 400), ('[]', 400),
                              ('{"revision":NaN}', 400), ('synthetic-private-malformed', 400), (' '*65537, 413)):
            response = self.client.post(URL, data=raw, content_type='application/json', secure=True,
                HTTP_ORIGIN='https://testserver', HTTP_X_CSRFTOKEN=token, HTTP_X_MAIPA_CLIENT_ACTOR=str(self.owner.pk))
            self.assertEqual(response.status_code, expected)
            self.assertNotIn(b'synthetic-private', response.content)
        self.assertEqual(Engagement.objects.count(), 0)

    def test_selection_is_owned_canonical_and_bounded_to_one_engagement(self):
        self.read()
        first = services.create_engagement(self.owner, {'name': 'First', 'entity_name': 'First entity'})
        second = services.create_engagement(self.owner, {'name': 'Second', 'entity_name': 'Second entity'})
        foreign = services.create_engagement(self.other, {'name': 'Foreign', 'entity_name': 'Foreign entity'})
        selected = self.read(str(second.pk))
        self.assertEqual(selected['selected_engagement_id'], str(second.pk))
        self.assertEqual([row['name'] for row in selected['entities']], ['Second entity'])
        self.assertEqual(len(selected['engagements']), 2)
        for query, status in ((str(foreign.pk), 404), (str(uuid.uuid4()), 404), ('../private', 400)):
            response = self.client.get(URL, {'engagement_id': query}, secure=True)
            self.assertEqual(response.status_code, status)
            self.assertNotIn(str(first.pk).encode(), response.content)
        self.assertEqual(self.client.get(URL, {'unexpected': 'x'}, secure=True).status_code, 400)

    def test_audit_failure_rolls_back_new_records_and_revision_without_private_diagnostics(self):
        initial = self.read()
        with patch('backend.maipa.intake.catalog.record_event', side_effect=DatabaseError('synthetic-private-database-path')):
            response = self.post(initial, 'create_engagement', name='Private submitted name', entity_name='Private submitted entity')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'error': {'code': 'client_catalog_unavailable'}})
        self.assertEqual(Engagement.objects.count(), 0)
        self.assertEqual(Entity.objects.count(), 0)
        self.assertEqual(self.read(), initial)

    def test_quota_refusal_rolls_back_shared_revision_without_creating_a_partial_entity(self):
        group = services.create_engagement(self.owner, {'name': 'Full engagement', 'entity_name': 'First entity'})
        Entity.objects.bulk_create([Entity(engagement=group, name=f'Synthetic {i}') for i in range(99)])
        previous = self.read(str(group.pk))
        response = self.post(previous, 'create_entity', engagement_id=str(group.pk), name='Too many')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Entity.objects.filter(engagement=group).count(), 100)
        self.assertEqual(self.read(str(group.pk)), previous)

    def test_existing_fact_values_and_revisions_are_unchanged_by_display_rename(self):
        created = self.create()
        entity = Entity.objects.get(engagement_id=created['engagement_id'])
        services.record_fact(self.owner, entity.pk, {'key': 'legal_form', 'knowledge': 'known', 'value': 'llc',
            'expected_revision': 0, 'effective_from': '2026-01-01', 'effective_to': None,
            'evidence': {'reference': 'synthetic-evidence', 'locator': 'Page 1', 'observed_on': '2026-01-01'}})
        before = list(FactRevision.objects.values())
        services.record_conclusion(self.owner, entity.pk, 'Synthetic retained conclusion',
                                   [FactRevision.objects.get().pk], date(2026, 10, 25))
        conclusions = list(Conclusion.objects.values())
        response = self.post(self.read(), 'rename_entity', entity_id=str(entity.pk), name='Display label only')
        self.assertEqual(response.status_code, 200)
        entity.refresh_from_db()
        self.assertEqual(entity.revision, 1)
        self.assertEqual(list(FactRevision.objects.values()), before)
        self.assertEqual(list(Conclusion.objects.values()), conclusions)
        self.assertEqual(self.client.get(f'/api/intake/entities/{entity.pk}/', secure=True).json()['facts']['legal_form']['value'], 'llc')
