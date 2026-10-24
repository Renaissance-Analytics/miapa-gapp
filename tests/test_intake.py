"""Independent P02 persistence/HTTP outcomes; no tax classification is inferred."""
import json
from datetime import date

from django.contrib.auth import get_user_model
from django.test import Client, TestCase


class IntakeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from backend.maipa.workspace.models import Installation, Membership
        cls.install = Installation.objects.create()
        cls.people = {}
        for role in ('administrator', 'preparer', 'reviewer', 'viewer'):
            user = get_user_model().objects.create_user(username='intake-' + role, password='synthetic-intake-password')
            Membership.objects.create(user=user, installation=cls.install, role=role)
            cls.people[role] = user

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.login('preparer')

    def login(self, role):
        self.client = Client(enforce_csrf_checks=True)
        token = self.client.get('/api/session/csrf/', secure=True).json()['csrf_token']
        response = self.client.post('/api/session/login/', data=json.dumps({'username': 'intake-' + role,
            'password': 'synthetic-intake-password'}), content_type='application/json', secure=True,
            HTTP_ORIGIN='https://testserver', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)
        self.token = response.json()['csrf_token']

    def post(self, path, body):
        return self.client.post(path, data=json.dumps(body), content_type='application/json', secure=True,
            HTTP_ORIGIN='https://testserver', HTTP_X_CSRFTOKEN=self.token)

    def create(self):
        response = self.post('/api/intake/engagements/', {'name': 'Synthetic annual engagement', 'entity_name': 'Synthetic Example LLC'})
        self.assertEqual(response.status_code, 201)
        self.engagement = response.json()['id']
        self.entity = response.json()['entities'][0]['id']
        return response.json()

    def assertion(self, key='legal_form', value='llc', version=0, **overrides):
        return {'key': key, 'knowledge': 'known', 'value': value, 'expected_revision': version,
            'effective_from': '2026-01-01', 'effective_to': None,
            'evidence': {'reference': 'synthetic-document-1', 'locator': 'page 2', 'observed_on': '2026-10-24'}} | overrides

    def record(self, body):
        return self.post(f'/api/intake/entities/{self.entity}/facts/', body)

    def profile(self, on='2026-10-24'):
        response = self.client.get(f'/api/intake/entities/{self.entity}/?on={on}', secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'no-store')
        return response.json()

    def test_typed_catalog_evidence_dates_and_multiple_entities_round_trip(self):
        self.create()
        values = {'legal_form': 'llc', 'tax_classification': 'partnership',
            'tax_elections': [{'name': 'Synthetic election', 'confirmed': False}],
            'ownership': [{'name': 'Synthetic owner', 'percentage': '60.1250'}],
            'periods': [{'name': '2026', 'start': '2026-01-01', 'end': '2026-12-31'}],
            'currency': 'USD', 'reporting_basis': 'accrual', 'jurisdictions': ['US', 'US-WI'],
            'industry': 'Synthetic services', 'systems': ['Synthetic source system'],
            'records_quality': 'incomplete', 'records_complete': False,
            'authorized_contacts': [{'name': 'Synthetic contact', 'role': 'Records contact', 'email': 'synthetic@example.invalid'}],
            'engagement_scope': 'Synthetic accounting preparation'}
        for version, (key, value) in enumerate(values.items()):
            response = self.record(self.assertion(key, value, version))
            self.assertEqual(response.status_code, 201, key)
        profile = self.profile()
        self.assertEqual(profile['revision'], len(values))
        self.assertEqual(set(profile['facts']), set(values))
        for key, value in values.items():
            self.assertEqual(profile['facts'][key]['value'], value)
            self.assertEqual(profile['facts'][key]['knowledge'], 'known')
            self.assertEqual(profile['facts'][key]['evidence'], self.assertion()['evidence'])
            self.assertEqual(profile['facts'][key]['effective_from'], '2026-01-01')
            self.assertIsNone(profile['facts'][key]['effective_to'])
        response = self.post(f'/api/intake/engagements/{self.engagement}/entities/', {'name': 'Synthetic second entity'})
        self.assertEqual(response.status_code, 201)
        listed = self.client.get('/api/intake/engagements/', secure=True).json()['engagements']
        self.assertEqual([e['name'] for e in listed[0]['entities']], ['Synthetic Example LLC', 'Synthetic second entity'])

    def test_false_is_known_and_unknown_requires_null_without_inference(self):
        self.create()
        self.assertEqual(self.record(self.assertion('records_complete', False)).status_code, 201)
        self.assertEqual(self.record(self.assertion(version=1)).status_code, 201)
        facts = self.profile()['facts']
        self.assertIs(facts['records_complete']['value'], False)
        self.assertEqual(facts['records_complete']['knowledge'], 'known')
        self.assertEqual(facts['tax_classification'], {'knowledge': 'unknown', 'value': None, 'revision_id': None})
        self.assertEqual(facts['tax_elections'], {'knowledge': 'unknown', 'value': None, 'revision_id': None})
        self.assertEqual(self.record(self.assertion('records_complete', None, 2, knowledge='unknown')).status_code, 201)
        self.assertEqual(self.profile()['facts']['records_complete']['knowledge'], 'unknown')
        self.assertIsNone(self.profile()['facts']['records_complete']['value'])

    def test_effective_intervals_future_assertions_and_corrections_preserve_history(self):
        self.create()
        first = self.record(self.assertion(effective_to='2027-01-01')).json()
        self.assertEqual(self.record(self.assertion(value='corporation', version=1,
            effective_from='2027-01-01')).status_code, 201)
        self.assertEqual(self.profile('2026-12-31')['facts']['legal_form']['value'], 'llc')
        self.assertEqual(self.profile('2027-01-01')['facts']['legal_form']['value'], 'corporation')
        self.assertEqual(self.profile('2025-12-31')['facts']['legal_form']['knowledge'], 'unknown')
        self.assertEqual(self.record(self.assertion(value='partnership', version=2,
            effective_from='2026-06-01', effective_to='2026-07-01')).status_code, 201)
        self.assertEqual(self.profile('2026-06-30')['facts']['legal_form']['value'], 'partnership')
        self.assertEqual(self.profile('2026-07-01')['facts']['legal_form']['value'], 'llc')
        history = self.client.get(f'/api/intake/entities/{self.entity}/facts/legal_form/history/', secure=True).json()
        self.assertEqual([r['value'] for r in history['revisions']], ['partnership', 'corporation', 'llc'])
        self.assertEqual(history['revisions'][-1]['id'], first['id'])
        self.assertEqual(history['revisions'][-1]['evidence'], self.assertion()['evidence'])
        self.assertTrue(all(r['recorded_at'] and r['recorded_by']==str(self.people['preparer'].pk) for r in history['revisions']))

    def test_expired_assertion_has_no_implicit_carry_forward(self):
        self.create()
        self.assertEqual(self.record(self.assertion(effective_to='2026-02-01')).status_code, 201)
        self.assertEqual(self.profile('2026-01-31')['facts']['legal_form']['value'], 'llc')
        self.assertEqual(self.profile('2026-02-01')['facts']['legal_form']['knowledge'], 'unknown')

    def test_stale_write_is_atomic_and_original_revision_cannot_be_mutated(self):
        from backend.maipa.intake.models import Entity, FactRevision
        self.create()
        first = self.record(self.assertion()).json()
        stale = self.record(self.assertion(value='corporation'))
        self.assertEqual((stale.status_code, stale.json()), (409, {'error': {'code': 'revision_conflict'}}))
        self.assertEqual(Entity.objects.get(pk=self.entity).revision, 1)
        self.assertEqual(FactRevision.objects.count(), 1)
        row = FactRevision.objects.get(pk=first['id'])
        row.value = 'corporation'
        with self.assertRaises(ValueError):
            row.save()
        row.refresh_from_db()
        self.assertEqual(row.value, 'llc')
        with self.assertRaises(ValueError):
            row.delete()

    def test_changed_fact_invalidates_only_its_real_dependents(self):
        from backend.maipa.intake.services import record_conclusion
        self.create()
        legal = self.record(self.assertion()).json()['id']
        currency = self.record(self.assertion('currency', 'USD', 1)).json()['id']
        known_false = self.record(self.assertion('records_complete', False, 2)).json()['id']
        affected = record_conclusion(self.people['preparer'], self.entity, 'Synthetic classification memo', [legal], date(2026, 10, 24))
        unrelated = record_conclusion(self.people['preparer'], self.entity, 'Synthetic currency memo', [currency], date(2026, 10, 24))
        false_basis = record_conclusion(self.people['preparer'], self.entity, 'Synthetic incomplete-records memo', [known_false], date(2026, 10, 24))
        self.assertEqual(self.record(self.assertion(value='corporation', version=3)).status_code, 201)
        affected.refresh_from_db(); unrelated.refresh_from_db(); false_basis.refresh_from_db()
        self.assertEqual(affected.status, 'stale')
        self.assertIsNotNone(affected.invalidated_at)
        self.assertEqual(unrelated.status, 'current')
        self.assertIsNone(unrelated.invalidated_at)
        self.assertEqual(false_basis.status, 'current')
        self.assertEqual(list(affected.dependencies.values_list('fact_revision_id', flat=True)), [__import__('uuid').UUID(legal)])

    def test_conclusions_refuse_unknown_stale_foreign_and_inapplicable_basis(self):
        from backend.maipa.intake.models import Conclusion
        from backend.maipa.intake.services import InvalidFact, record_conclusion
        self.create()
        first = self.record(self.assertion()).json()['id']
        self.assertEqual(self.record(self.assertion(value='corporation', version=1)).status_code, 201)
        unknown = self.record(self.assertion('currency', None, 2, knowledge='unknown')).json()['id']
        future = self.record(self.assertion('industry', 'Synthetic future', 3, effective_from='2027-01-01')).json()['id']
        for ids, user in (([first], self.people['preparer']), ([unknown], self.people['preparer']),
                          ([future], self.people['preparer']), ([future], self.people['administrator']), ([], self.people['preparer'])):
            with self.assertRaises(InvalidFact):
                record_conclusion(user, self.entity, 'Synthetic invalid basis', ids, date(2026, 10, 24))
        self.assertEqual(self.profile()['stale_conclusions'], 0)
        self.assertEqual(Conclusion.objects.count(), 0)

    def test_invalid_fact_schema_dates_values_and_evidence_make_no_changes(self):
        from backend.maipa.intake.models import FactRevision
        self.create()
        invalid = [self.assertion(knowledge='unknown'), self.assertion(value=None), self.assertion(key='owner'),
            self.assertion(expected_revision=True), self.assertion(effective_from='2026-02-30'),
            self.assertion(effective_to='2026-01-01'), self.assertion(effective_from='2026-1-1'),
            self.assertion(evidence={'reference': '', 'locator': '', 'observed_on': '2026-10-24'}),
            self.assertion(evidence={'reference': 'x', 'locator': 'x', 'observed_on': '2026-10-24', 'owner': 'foreign'}),
            self.assertion('records_complete', 'false'), self.assertion('records_complete', 0),
            self.assertion('currency', 'usd'), self.assertion('reporting_basis', 'guessed'),
            self.assertion('ownership', [{'name': 'A', 'percentage': 60.1}]),
            self.assertion('ownership', [{'name': 'A', 'percentage': 'NaN'}]),
            self.assertion('ownership', [{'name': 'A', 'percentage': '100.0001'}]),
            self.assertion('ownership', [{'name': 'A', 'percentage': '60'}, {'name': 'B', 'percentage': '50'}]),
            self.assertion('periods', [{'name': 'Bad', 'start': '2026-12-31', 'end': '2026-01-01'}]),
            self.assertion('tax_elections', [{'name': 'x', 'confirmed': 'false'}]),
            self.assertion('authorized_contacts', [{'name': 'x', 'role': 'x', 'email': 'bad'}]),
            self.assertion(owner=str(self.people['administrator'].pk))]
        for body in invalid:
            response = self.record(body)
            self.assertEqual((response.status_code, response.json()), (400, {'error': {'code': 'invalid_request'}}), body['key'])
        self.assertEqual(FactRevision.objects.count(), 0)
        self.assertEqual(self.profile()['revision'], 0)

    def test_user_and_installation_hints_never_expose_foreign_engagements(self):
        self.create()
        owner_client = self.client
        self.login('administrator')
        response = self.client.get(f'/api/intake/engagements/?owner={self.people["preparer"].pk}&tenant_id=foreign', secure=True,
            HTTP_X_USER_ID=str(self.people['preparer'].pk), HTTP_X_ROLE='preparer')
        self.assertEqual(response.json(), {'engagements': []})
        for path in (f'/api/intake/entities/{self.entity}/', f'/api/intake/entities/{self.entity}/facts/legal_form/history/'):
            response = self.client.get(path, secure=True)
            self.assertEqual((response.status_code, response.json()), (404, {'error': {'code': 'not_found'}}))
        self.assertEqual(self.record(self.assertion()).status_code, 404)
        self.assertEqual(self.post(f'/api/intake/engagements/{self.engagement}/entities/', {'name': 'Foreign'}).status_code, 404)
        self.assertEqual(owner_client.get(f'/api/intake/entities/{self.entity}/', secure=True).status_code, 200)

    def test_live_role_revocation_anonymous_csrf_and_forged_create_are_denied(self):
        from backend.maipa.intake.models import Engagement
        from backend.maipa.workspace.models import Membership
        self.create()
        Membership.objects.filter(user=self.people['preparer']).update(role='viewer')
        self.assertEqual(self.profile()['revision'], 0)
        self.assertEqual(self.record(self.assertion()).status_code, 403)
        self.assertEqual(self.post('/api/intake/engagements/', {'name': 'x', 'entity_name': 'x'}).status_code, 403)
        Membership.objects.filter(user=self.people['preparer']).update(enabled=False)
        self.assertEqual(self.client.get('/api/intake/engagements/', secure=True).status_code, 403)
        self.login('administrator')
        before = Engagement.objects.count()
        self.assertEqual(self.post('/api/intake/engagements/', {'name': 'x', 'entity_name': 'x', 'owner': 'foreign'}).status_code, 400)
        self.assertEqual(Engagement.objects.count(), before)
        self.assertEqual(self.client.post('/api/intake/engagements/', data='{}', content_type='application/json', secure=True).status_code, 403)
        self.assertEqual(Client().get('/api/intake/engagements/', secure=True).status_code, 401)

    def test_bounded_json_reads_history_cursor_and_methods(self):
        self.create()
        path = f'/api/intake/entities/{self.entity}/facts/'
        for body in ('[]', '{"key":"legal_form","key":"currency"}', '{"x":NaN}'):
            response = self.client.post(path, data=body, content_type='application/json', secure=True,
                HTTP_ORIGIN='https://testserver', HTTP_X_CSRFTOKEN=self.token)
            self.assertEqual(response.status_code, 400)
        response = self.client.post(path, data=' '*65537, content_type='application/json', secure=True,
            HTTP_ORIGIN='https://testserver', HTTP_X_CSRFTOKEN=self.token)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.client.get(path, secure=True).status_code, 405)
        self.assertEqual(self.client.delete(f'/api/intake/entities/{self.entity}/', secure=True,
            HTTP_ORIGIN='https://testserver', HTTP_X_CSRFTOKEN=self.token).status_code, 405)
        self.assertEqual(self.client.get(f'/api/intake/entities/{self.entity}/?on=bad', secure=True).status_code, 400)
        self.assertEqual(self.client.get(f'/api/intake/entities/{self.entity}/facts/legal_form/history/?before=bad', secure=True).status_code, 400)
        for version in range(3):
            self.assertEqual(self.record(self.assertion(version=version)).status_code, 201)
        history = self.client.get(f'/api/intake/entities/{self.entity}/facts/legal_form/history/?before=3', secure=True).json()
        self.assertEqual([r['revision'] for r in history['revisions']], [2, 1])
        self.assertIsNone(history['next_before'])
