"""HTTP authority boundaries use actual sessions, CSRF and persisted scope."""
from datetime import timedelta
import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone


class PolicyHttpTests(TestCase):
    def setUp(self):
        from backend.maipa.workspace.models import Installation, Membership
        from backend.maipa.intake.models import Engagement, Entity
        from backend.maipa.policies.authority import provision_root
        self.install = Installation.objects.create()
        self.user = get_user_model().objects.create_user(username='policy-owner')
        self.other = get_user_model().objects.create_user(username='policy-other')
        for user in (self.user, self.other):
            Membership.objects.create(user=user, installation=self.install, role='administrator')
        engagement = Engagement.objects.create(installation=self.install, owner=self.user, name='Synthetic policy engagement')
        self.entity = Entity.objects.create(engagement=engagement, name='Synthetic private entity')
        self.path = f'/api/policies/entities/{self.entity.pk}/'
        self.now = timezone.now()
        self.grant = provision_root(entity_id=self.entity.pk, subject_id=self.user.pk,
            action='ledger.post', duty='approver', starts_at=self.now-timedelta(hours=1),
            expires_at=self.now+timedelta(days=1), limit='1000.0000', currency='USD',
            evidence='synthetic signed policy', can_delegate=True)
        self.client = Client(enforce_csrf_checks=True)

    def login(self, user=None):
        self.client.force_login(user or self.user)
        self.token = self.client.get('/api/session/csrf/', secure=True).json()['csrf_token']

    def post(self, path, value, **headers):
        return self.client.post(path, data=json.dumps(value), content_type='application/json', secure=True,
            HTTP_ORIGIN='https://testserver', HTTP_X_CSRFTOKEN=self.token, **headers)

    def test_anonymous_and_foreign_admin_cannot_read_private_authority(self):
        self.assertEqual(self.client.get(self.path, secure=True).status_code, 401)
        self.login(self.other)
        response = self.client.get(self.path, secure=True, HTTP_X_MAIPA_OWNER=str(self.user.pk), HTTP_X_MAIPA_ROLE='administrator')
        self.assertEqual(response.status_code, 404)
        self.assertNotIn('synthetic signed policy', response.content.decode())

    def test_summary_is_current_private_and_does_not_claim_execution(self):
        self.login()
        response = self.client.get(self.path, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'no-store')
        value = response.json()
        self.assertFalse(value['financial_execution_available'])
        self.assertEqual(value['grants'][0]['id'], str(self.grant.pk))
        self.assertTrue(value['grants'][0]['current'])
        self.assertEqual((value['grants'][0]['limit'], value['grants'][0]['currency']), ('1000.0000', 'USD'))

    def test_revocation_requires_csrf_and_applies_without_relogin(self):
        self.login()
        path = f'/api/policies/grants/{self.grant.pk}/revoke/'
        self.assertEqual(self.client.post(path, data='{}', content_type='application/json', secure=True).status_code, 403)
        self.assertEqual(self.post(path, {'reason': 'synthetic withdrawn permission'}).status_code, 200)
        self.assertFalse(self.client.get(self.path, secure=True).json()['grants'][0]['current'])
        self.assertEqual(self.post(path, {'reason': 'safe repeated withdrawal'}).status_code, 200)

    def test_http_cannot_create_root_authority_or_choose_agent_identity(self):
        self.login()
        self.assertEqual(self.post(self.path, {'role': 'approver', 'root': True}).status_code, 405)
        path = f'/api/policies/grants/{self.grant.pk}/revoke/'
        response = self.post(path, {'reason': 'synthetic', 'actor': 'operator', 'kind': 'agent'})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(self.client.get(self.path, secure=True).json()['grants'][0]['current'])

    def test_delegation_is_narrowed_and_foreign_administrator_cannot_revoke(self):
        self.login()
        path = f'/api/policies/grants/{self.grant.pk}/delegate/'
        value = {'subject_id': str(self.other.pk), 'starts_at': self.now.isoformat(),
            'expires_at': (self.now+timedelta(hours=1)).isoformat(), 'limit': '100.0000',
            'currency': 'USD', 'evidence': 'synthetic bounded delegation'}
        response = self.post(path, value)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['parent_id'], str(self.grant.pk))
        self.assertEqual(self.post(path, value | {'limit': '1000.0001'}).status_code, 403)
        self.login(self.other)
        self.assertEqual(self.post(f'/api/policies/grants/{self.grant.pk}/revoke/', {'reason': 'foreign attempt'}).status_code, 403)
