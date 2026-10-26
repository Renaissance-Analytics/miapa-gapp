"""Independent P05 outcomes for owned durable personal presentation."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading

from django.contrib.auth import get_user_model
from django.db import connections
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, TransactionTestCase

from backend.maipa.workspace.models import Installation, Membership

URL = '/api/workspace/preferences/'


class WorkspacePreferencesTests(TestCase):
    def setUp(self):
        self.installation = Installation.objects.create()
        self.owner = get_user_model().objects.create_user(username='preferences-owner')
        self.other = get_user_model().objects.create_user(username='preferences-reader')
        Membership.objects.create(user=self.owner, installation=self.installation, role='preparer')
        Membership.objects.create(user=self.other, installation=self.installation, role='viewer')
        self.client.force_login(self.owner)

    def state(self, client=None):
        response = (client or self.client).get(URL, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'no-store')
        return response.json()

    def save(self, previous, theme='dark', view='workspace-guide', **extra):
        return self.client.post(URL, data=json.dumps({'expected_revision': previous['revision'],
            'theme': theme, 'active_view': view} | extra), content_type='application/json', secure=True,
            HTTP_X_MAIPA_PREFERENCES=previous['preferences_id'])

    def test_defaults_and_saved_choices_survive_an_independent_login_without_changing_width(self):
        initial = self.state()
        self.assertEqual(set(initial), {'preferences_id', 'user_id', 'revision', 'theme', 'active_view'})
        self.assertRegex(initial['preferences_id'], r'^[0-9a-f-]{36}$')
        self.assertEqual((initial['user_id'], initial['revision'], initial['theme'], initial['active_view']),
                         (str(self.owner.pk), 0, 'system', 'client-facts'))
        width = self.client.get('/api/workspace/layout/', secure=True).json()
        saved = self.save(initial)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json(), initial | {'revision': 1, 'theme': 'dark', 'active_view': 'workspace-guide'})
        self.assertEqual(self.client.get('/api/workspace/layout/', secure=True).json(), width)
        self.client.logout()
        self.assertEqual(self.client.get(URL, secure=True).status_code, 401)
        independent = Client()
        independent.force_login(self.owner)
        self.assertEqual(self.state(independent), saved.json())

    def test_viewer_and_old_tab_stay_in_separate_owned_preferences(self):
        previous = self.state()
        self.assertEqual(self.save(previous).status_code, 200)
        self.client.force_login(self.other)
        other = self.state()
        self.assertNotEqual(other['preferences_id'], previous['preferences_id'])
        self.assertEqual((other['theme'], other['active_view']), ('system', 'client-facts'))
        self.assertEqual(self.save(previous | {'revision': 0}).status_code, 409)
        self.assertEqual(self.state(), other)
        self.assertEqual(self.save(other, 'light', 'client-facts').status_code, 200)
        self.assertEqual(Membership.objects.get(user=self.other).role, 'viewer')
        self.client.force_login(self.owner)
        self.assertEqual((self.state()['theme'], self.state()['active_view']), ('dark', 'workspace-guide'))

    def test_stale_write_preserves_the_winner_until_explicit_reread(self):
        first = self.state()
        winner = self.save(first)
        self.assertEqual(winner.status_code, 200)
        self.assertEqual(self.save(first, 'light', 'client-facts').status_code, 409)
        self.assertEqual(self.state(), winner.json())
        self.assertEqual(self.save(self.state(), 'system', 'client-facts').json()['revision'], 2)

    def test_unknown_choices_credentials_paths_and_authority_fields_never_write(self):
        initial = self.state()
        for theme in ('auto', '', '<script>', True, None, [], {'theme': 'dark'}):
            self.assertEqual(self.save(initial, theme).status_code, 400)
        for view in ('unknown', '../client-facts', 'https://foreign.invalid', 'Maipa.Note', True, None, []):
            self.assertEqual(self.save(initial, view=view).status_code, 400)
        for extra in ({'user_id': str(self.other.pk)}, {'installation_id': str(self.installation.pk)},
                      {'path': 'providers/private'}, {'token': 'synthetic-only'}, {'schema': {}},
                      {'role': 'administrator'}, {'entity_id': 'untrusted'}):
            self.assertEqual(self.save(initial, **extra).status_code, 400)
        self.assertEqual(self.state(), initial)

    def test_revision_and_strict_json_guards_do_not_advance_the_saved_choices(self):
        initial = self.state()
        for revision in (True, -1, 2_000_000_000, '0', None):
            self.assertEqual(self.save(initial, expected_revision=revision).status_code, 400)
        for raw, status in [('[]', 400), ('null', 400), ('{"expected_revision":0,"expected_revision":1,"theme":"dark","active_view":"client-facts"}', 400),
                            ('{"expected_revision":NaN,"theme":"dark","active_view":"client-facts"}', 400), (' ' * 4097, 413)]:
            response = self.client.post(URL, data=raw, content_type='application/json', secure=True,
                HTTP_X_MAIPA_PREFERENCES=initial['preferences_id'])
            self.assertEqual(response.status_code, status)
        self.assertEqual(self.state(), initial)

    def test_missing_or_foreign_identity_cannot_initialize_or_replace_preferences(self):
        body = json.dumps({'expected_revision': 0, 'theme': 'dark', 'active_view': 'client-facts'})
        self.assertEqual(self.client.post(URL, data=body, content_type='application/json', secure=True).status_code, 409)
        initial = self.state()
        for headers in ({}, {'HTTP_X_MAIPA_PREFERENCES': 'invalid'},
                        {'HTTP_X_MAIPA_PREFERENCES': '12345678-1234-4234-8234-123456789012'}):
            self.assertEqual(self.client.post(URL, data=body, content_type='application/json', secure=True, **headers).status_code, 409)
        self.assertEqual(self.state(), initial)

    def test_authentication_csrf_origin_methods_and_live_revocation_apply(self):
        self.assertEqual(Client().get(URL, secure=True).status_code, 401)
        initial = self.state()
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.owner)
        body = json.dumps({'expected_revision': 0, 'theme': 'dark', 'active_view': 'workspace-guide'})
        self.assertEqual(protected.post(URL, data=body, content_type='application/json', secure=True,
            HTTP_X_MAIPA_PREFERENCES=initial['preferences_id']).status_code, 403)
        token = protected.get('/api/session/csrf/', secure=True).json()['csrf_token']
        self.assertEqual(protected.post(URL, data=body, content_type='application/json', secure=True,
            HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN='https://foreign.invalid',
            HTTP_X_MAIPA_PREFERENCES=initial['preferences_id']).status_code, 403)
        self.assertEqual(self.state(), initial)
        Membership.objects.filter(user=self.owner).update(enabled=False)
        self.assertEqual(self.client.get(URL, secure=True).status_code, 403)
        self.assertEqual(self.save(initial).status_code, 403)
        Membership.objects.filter(user=self.owner).update(enabled=True)
        response = self.client.put(URL, data='{}', content_type='application/json', secure=True)
        self.assertEqual((response.status_code, response['Allow']), (405, 'GET, POST'))
        self.assertEqual(self.state(), initial)

    def test_mismatched_install_binding_is_denied_without_rebinding(self):
        from backend.maipa.workspace.models import WorkspacePreferences
        initial = self.state()
        # A damaged/misbound record must not be silently adopted by GET or POST.
        WorkspacePreferences.objects.filter(user=self.owner).update(installation_id=999999)
        self.assertEqual(self.client.get(URL, secure=True).status_code, 403)
        self.assertEqual(self.save(initial).status_code, 409)
        WorkspacePreferences.objects.filter(user=self.owner).update(installation=self.installation)
        self.assertEqual(self.state(), initial)

    def test_database_rejects_unsupported_choices_and_exhausted_revisions(self):
        from backend.maipa.workspace.models import WorkspacePreferences
        initial = self.state()
        for changes in ({'theme': 'arbitrary'}, {'active_view': 'unknown-view'}, {'revision': 2_000_000_000}):
            with self.assertRaises(IntegrityError), transaction.atomic():
                WorkspacePreferences.objects.filter(user=self.owner).update(**changes)
        self.assertEqual(self.state(), initial)


class ConcurrentPreferencesTests(TransactionTestCase):
    def test_real_concurrent_connections_accept_at_most_one_revision(self):
        installation = Installation.objects.create()
        owner = get_user_model().objects.create_user(username='racing-preferences')
        Membership.objects.create(user=owner, installation=installation, role='preparer')
        clients = [Client(), Client()]
        for client in clients:
            client.force_login(owner)
        response = clients[0].get(URL, secure=True)
        self.assertEqual(response.status_code, 200)
        initial = response.json()
        barrier = threading.Barrier(2)
        def attempt(index):
            try:
                barrier.wait(timeout=10)
                response = clients[index].post(URL, data=json.dumps({'expected_revision': 0,
                    'theme': ('dark', 'light')[index], 'active_view': 'workspace-guide'}),
                    content_type='application/json', secure=True, HTTP_X_MAIPA_PREFERENCES=initial['preferences_id'])
                return response.status_code
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(attempt, (0, 1)))
        self.assertTrue(all(status in (200, 409, 503) for status in statuses))
        self.assertLessEqual(statuses.count(200), 1)
        saved = clients[0].get(URL, secure=True).json()
        self.assertEqual(saved['revision'], statuses.count(200))
        self.assertEqual(saved['preferences_id'], initial['preferences_id'])
