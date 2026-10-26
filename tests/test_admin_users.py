"""Independent P06 acceptance for the local administrator's access controls."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connections, transaction
from django.test import Client, TestCase, TransactionTestCase

from backend.maipa.workspace.models import Installation, Membership

URL = '/api/admin/users/'
PASSWORD = 'Local-fixture-only-49!alpha'


class AdminUsersTests(TestCase):
    def setUp(self):
        self.installation = Installation.objects.create()
        self.admin = get_user_model().objects.create_user(username='local-administrator')
        self.reader = get_user_model().objects.create_user(username='local-reader')
        Membership.objects.create(user=self.admin, installation=self.installation, role='administrator')
        Membership.objects.create(user=self.reader, installation=self.installation, role='viewer')
        self.client.force_login(self.admin)

    def state(self, client=None):
        response = (client or self.client).get(URL, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'no-store')
        return response.json()

    def write(self, state, *, target=None, client=None, **changes):
        body = {'expected_revision': state['revision'], 'installation_id': state['installation_id']} | changes
        return (client or self.client).post(URL if target is None else f'{URL}{target}/',
            data=json.dumps(body), content_type='application/json', secure=True,
            HTTP_X_MAIPA_ADMIN=state['actor_id'])

    def create(self, state, **changes):
        return self.write(state, username='new-local-user', password=PASSWORD,
            confirmation=PASSWORD, role='preparer', **changes)

    def change(self, state, user=None, **changes):
        return self.write(state, target=(user or self.reader).pk,
            **({'role': 'reviewer', 'enabled': True} | changes))

    def test_read_exposes_only_bounded_membership_and_safe_audit_fields(self):
        state = self.state()
        self.assertEqual(set(state), {'actor_id', 'installation_id', 'revision', 'users', 'events'})
        self.assertEqual((state['actor_id'], state['installation_id'], state['revision']),
                         (str(self.admin.pk), str(self.installation.pk), 0))
        self.assertEqual(state['events'], [])
        self.assertEqual(state['users'], [
            {'id': str(self.admin.pk), 'username': self.admin.username, 'role': 'administrator', 'enabled': True, 'active': True},
            {'id': str(self.reader.pk), 'username': self.reader.username, 'role': 'viewer', 'enabled': True, 'active': True}])

    def test_create_persists_validated_password_role_and_safe_audit(self):
        initial = self.state()
        response = self.create(initial)
        self.assertEqual((response.status_code, response.json()), (201, {'status': 'saved', 'revision': 1}))
        user = get_user_model().objects.get(username='new-local-user')
        self.assertTrue(user.check_password(PASSWORD))
        self.assertNotEqual(user.password, PASSWORD)
        self.assertEqual((user.is_active, user.is_staff, user.is_superuser), (True, False, False))
        self.assertEqual(Membership.objects.get(user=user).role, 'preparer')
        independent = Client(); independent.force_login(self.admin)
        saved = self.state(independent)
        self.assertEqual((saved['revision'], len(saved['users']), len(saved['events'])), (1, 3, 1))
        event = saved['events'][0]
        self.assertEqual(set(event), {'actor_id', 'subject_id', 'kind', 'role', 'enabled', 'revision', 'at'})
        self.assertEqual({k: event[k] for k in event if k != 'at'}, {'actor_id': str(self.admin.pk),
            'subject_id': str(user.pk), 'kind': 'created', 'role': 'preparer', 'enabled': True, 'revision': 1})
        self.assertNotIn(PASSWORD, json.dumps(saved))
        self.assertTrue(Client().login(username='new-local-user', password=PASSWORD))

    def test_password_name_and_confirmation_validation_never_create_partial_users(self):
        state = self.state()
        fields = {'username': 'new-local-user', 'password': PASSWORD, 'confirmation': PASSWORD, 'role': 'viewer'}
        for change in ({'username': self.reader.username}, {'username': 'invalid space'}, {'username': ''},
                       {'password': 'short', 'confirmation': 'short'}, {'confirmation': 'different'},
                       {'username': 'a' * 151}, {'password': 'x' * 1025, 'confirmation': 'x' * 1025}):
            response = self.write(state, **(fields | change))
            self.assertEqual(response.status_code, 400)
        self.assertEqual(get_user_model().objects.count(), 2)
        self.assertEqual(self.state(), state)

    def test_restricted_roles_flags_and_current_revocation_never_grant_administration(self):
        self.assertEqual(Client().get(URL, secure=True).status_code, 401)
        self.reader.is_staff = self.reader.is_superuser = True
        self.reader.save()
        self.client.force_login(self.reader)
        state = {'revision': 0, 'installation_id': str(self.installation.pk), 'actor_id': str(self.reader.pk)}
        for role in ('viewer', 'reviewer', 'preparer'):
            Membership.objects.filter(user=self.reader).update(role=role)
            self.assertEqual(self.client.get(URL, secure=True).status_code, 403)
            self.assertEqual(self.change(state).status_code, 403)
        Membership.objects.filter(user=self.reader).update(role='administrator', enabled=False)
        self.assertEqual(self.client.get(URL, secure=True).status_code, 403)
        Membership.objects.filter(user=self.reader).delete()
        self.assertEqual(self.client.get(URL, secure=True).status_code, 403)

    def test_wrong_actor_cookie_and_installation_binding_cannot_write(self):
        initial = self.state()
        Membership.objects.filter(user=self.reader).update(role='administrator')
        self.client.force_login(self.reader)
        self.assertEqual(self.change(initial).status_code, 409)
        self.client.force_login(self.admin)
        self.assertEqual(self.change(initial | {'installation_id': '12345678-1234-4234-8234-123456789012'}).status_code, 409)
        self.assertEqual(self.state()['revision'], 0)

    def test_stale_revision_cannot_overwrite_winner_or_append_audit(self):
        initial = self.state()
        self.assertEqual(self.change(initial).status_code, 200)
        saved = self.state()
        self.assertEqual(self.change(initial, role='administrator').status_code, 409)
        self.assertEqual(self.state(), saved)
        self.assertEqual((saved['revision'], saved['users'][1]['role'], len(saved['events'])), (1, 'reviewer', 1))

    def test_strict_json_bounds_unknown_fields_and_invalid_types_make_no_change(self):
        initial = self.state()
        for extra in ({'owner_id': str(self.reader.pk)}, {'path': '../private'}, {'token': 'fixture'},
                      {'is_superuser': True}, {'schema': {}}, {'provider': 'codex'}, {'role': []},
                      {'role': 'owner'}, {'enabled': 'false'}, {'expected_revision': True},
                      {'expected_revision': -1}, {'expected_revision': 2_000_000_000}):
            self.assertEqual(self.change(initial, **extra).status_code, 400)
        for raw, status in [('[]', 400), ('null', 400), ('{"role":"viewer","role":"administrator"}', 400),
                            ('{"expected_revision":NaN}', 400), (' ' * 4097, 413)]:
            response = self.client.post(URL, data=raw, content_type='application/json', secure=True,
                HTTP_X_MAIPA_ADMIN=initial['actor_id'])
            self.assertEqual(response.status_code, status)
        self.assertEqual(self.state(), initial)

    def test_last_active_administrator_is_preserved_and_valid_self_demotion_loses_access(self):
        initial = self.state()
        for changes in ({'role': 'viewer'}, {'enabled': False}):
            self.assertEqual(self.change(initial, self.admin, **changes).status_code, 409)
        Membership.objects.filter(user=self.reader).update(role='administrator', enabled=False)
        self.assertEqual(self.change(initial, self.admin).status_code, 409)
        Membership.objects.filter(user=self.reader).update(enabled=True)
        get_user_model().objects.filter(pk=self.reader.pk).update(is_active=False)
        self.assertEqual(self.change(initial, self.admin).status_code, 409)
        get_user_model().objects.filter(pk=self.reader.pk).update(is_active=True)
        self.assertEqual(self.change(initial, self.admin).status_code, 200)
        self.assertEqual(self.client.get(URL, secure=True).status_code, 403)
        self.assertEqual(Membership.objects.get(user=self.admin).role, 'reviewer')

    def test_disabled_membership_takes_effect_in_existing_session(self):
        reader = Client(); reader.force_login(self.reader)
        self.assertEqual(reader.get('/api/workspace/', secure=True).status_code, 200)
        self.assertEqual(self.change(self.state(), enabled=False).status_code, 200)
        self.assertEqual(reader.get('/api/workspace/', secure=True).status_code, 403)
        self.assertEqual(self.state()['users'][1]['enabled'], False)

    def test_absent_or_foreign_member_is_not_adopted(self):
        initial = self.state()
        outsider = get_user_model().objects.create_user(username='no-installation')
        self.assertEqual(self.change(initial, outsider).status_code, 404)
        Membership.objects.filter(user=self.reader).update(installation_id='12345678-1234-4234-8234-123456789012')
        self.assertEqual(self.change(initial).status_code, 404)
        Membership.objects.filter(user=self.reader).update(installation=self.installation)
        self.assertEqual(self.state(), initial)

    def test_csrf_origin_and_method_guards_preserve_state(self):
        initial = self.state()
        protected = Client(enforce_csrf_checks=True); protected.force_login(self.admin)
        self.assertEqual(self.change(initial, client=protected).status_code, 403)
        token = protected.get('/api/session/csrf/', secure=True).json()['csrf_token']
        response = protected.post(URL, data='{}', content_type='application/json', secure=True,
            HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN='https://foreign.invalid')
        self.assertEqual(response.status_code, 403)
        response = self.client.delete(URL, secure=True)
        self.assertEqual((response.status_code, response['Allow']), (405, 'GET, POST'))
        self.assertEqual(self.state(), initial)

    def test_audit_failure_rolls_back_user_membership_and_revision(self):
        initial = self.state()
        with patch('backend.maipa.workspace.access.AccessEvent.objects.create', side_effect=IntegrityError('fixture')):
            self.assertEqual(self.create(initial).status_code, 503)
        self.assertEqual(get_user_model().objects.count(), 2)
        self.assertEqual(self.state(), initial)

    def test_user_limit_and_revision_exhaustion_fail_without_partial_change(self):
        initial = self.state()
        User = get_user_model()
        for i in range(198):
            user = User.objects.create(username=f'fixture-limit-{i}', password='!')
            Membership.objects.create(user=user, installation=self.installation, role='viewer')
        self.assertEqual(self.create(initial).status_code, 409)
        self.assertEqual(len(self.state()['users']), 200)
        self.assertEqual(self.state()['revision'], 0)
        Installation.objects.filter(pk=self.installation.pk).update(access_revision=1_999_999_999)
        self.assertEqual(self.change(initial | {'revision': 1_999_999_999}).status_code, 409)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Installation.objects.filter(pk=self.installation.pk).update(access_revision=2_000_000_000)


class ConcurrentAdminUsersTests(TransactionTestCase):
    def test_concurrent_administrators_cannot_remove_each_others_last_authority(self):
        install = Installation.objects.create()
        users = [get_user_model().objects.create_user(username=f'concurrent-admin-{i}') for i in range(2)]
        clients = [Client(), Client()]
        for user, client in zip(users, clients):
            Membership.objects.create(user=user, installation=install, role='administrator')
            client.force_login(user)
        first = clients[0].get(URL, secure=True)
        self.assertEqual(first.status_code, 200)
        barrier = threading.Barrier(2)
        def attempt(i):
            try:
                barrier.wait(timeout=10)
                return clients[i].post(f'{URL}{users[1-i].pk}/', data=json.dumps({
                    'expected_revision': 0, 'installation_id': str(install.pk), 'role': 'viewer', 'enabled': True}),
                    content_type='application/json', secure=True, HTTP_X_MAIPA_ADMIN=str(users[i].pk)).status_code
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(attempt, (0, 1)))
        self.assertTrue(all(status in (200, 403, 409, 503) for status in statuses))
        self.assertLessEqual(statuses.count(200), 1)
        self.assertGreaterEqual(Membership.objects.filter(role='administrator', enabled=True, user__is_active=True).count(), 1)
        install.refresh_from_db()
        self.assertEqual(install.access_revision, statuses.count(200))
