"""Independent P04 outcomes: durable personal layout, never financial authority."""
import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from backend.maipa.workspace.models import Installation, Membership


class WorkspaceLayoutTests(TestCase):
    def setUp(self):
        self.installation = Installation.objects.create()
        self.owner = get_user_model().objects.create_user(username='layout-owner')
        self.other = get_user_model().objects.create_user(username='layout-reader')
        Membership.objects.create(user=self.owner, installation=self.installation, role='preparer')
        Membership.objects.create(user=self.other, installation=self.installation, role='viewer')
        self.client.force_login(self.owner)

    def state(self, client=None):
        response = (client or self.client).get('/api/workspace/layout/', secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'no-store')
        return response.json()

    def save(self, previous, points, **changes):
        return self.client.post('/api/workspace/layout/', data=json.dumps({
            'expected_revision': previous['revision'], 'split_basis_points': points} | changes),
            content_type='application/json', secure=True, HTTP_X_MAIPA_LAYOUT=previous['layout_id'])

    def test_personal_width_survives_logout_and_an_independent_session(self):
        initial = self.state()
        self.assertEqual(set(initial), {'layout_id', 'user_id', 'revision', 'split_basis_points'})
        self.assertEqual(initial['user_id'], str(self.owner.pk))
        self.assertEqual((initial['revision'], initial['split_basis_points']), (0, 2800))
        self.assertRegex(initial['layout_id'], r'^[0-9a-f-]{36}$')
        saved = self.save(initial, 3375)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json(), initial | {'revision': 1, 'split_basis_points': 3375})
        self.client.logout()
        self.assertEqual(self.client.get('/api/workspace/layout/', secure=True).status_code, 401)
        independent = Client()
        independent.force_login(self.owner)
        self.assertEqual(self.state(independent), saved.json())

    def test_other_user_and_old_tab_cannot_replace_each_others_layout(self):
        previous = self.state()
        self.assertEqual(self.save(previous, 3900).status_code, 200)
        self.client.force_login(self.other)
        other = self.state()
        self.assertNotEqual(other['layout_id'], previous['layout_id'])
        self.assertEqual(other['split_basis_points'], 2800)
        response = self.save(previous | {'revision': 0}, 4400)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.state(), other)
        self.assertEqual(self.save(other, 2200).status_code, 200)
        self.assertEqual(Membership.objects.get(user=self.other).role, 'viewer')
        self.client.force_login(self.owner)
        self.assertEqual(self.state()['split_basis_points'], 3900)

    def test_revision_conflict_preserves_the_winner_and_requires_reread(self):
        initial = self.state()
        winner = self.save(initial, 4500)
        self.assertEqual(winner.status_code, 200)
        loser = self.save(initial, 2200)
        self.assertEqual(loser.status_code, 409)
        self.assertEqual(self.state(), winner.json())
        self.assertEqual(self.save(self.state(), 2200).status_code, 200)
        self.assertEqual(self.state()['revision'], 2)

    def test_invalid_widths_identity_hints_and_revision_types_do_not_write(self):
        initial = self.state()
        for points in (2199, 4501, True, '3300', 33.3, None):
            self.assertEqual(self.save(initial, points).status_code, 400)
        for changes in ({'expected_revision': True}, {'expected_revision': -1},
                        {'expected_revision': 2_000_000_000}, {'user_id': str(self.other.pk)},
                        {'installation_id': str(self.installation.pk)}, {'role': 'administrator'}):
            self.assertEqual(self.save(initial, 3300, **changes).status_code, 400)
        self.assertEqual(self.state(), initial)

    def test_strict_bounded_json_and_missing_identity_never_update_layout(self):
        initial = self.state()
        for raw, status in [('[]', 400), ('null', 400), ('{"expected_revision":0,"expected_revision":1,"split_basis_points":3300}', 400),
                            ('{"expected_revision":0,"split_basis_points":NaN}', 400), (' ' * 4097, 413)]:
            response = self.client.post('/api/workspace/layout/', data=raw,
                content_type='application/json', secure=True, HTTP_X_MAIPA_LAYOUT=initial['layout_id'])
            self.assertEqual(response.status_code, status)
        for headers in ({}, {'HTTP_X_MAIPA_LAYOUT': 'invalid'}):
            response = self.client.post('/api/workspace/layout/', data=json.dumps({
                'expected_revision': 0, 'split_basis_points': 3300}), content_type='application/json', secure=True, **headers)
            self.assertEqual(response.status_code, 409)
        self.assertEqual(self.state(), initial)

    def test_live_membership_authentication_csrf_and_method_guards_apply(self):
        initial = self.state()
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.owner)
        body = json.dumps({'expected_revision': 0, 'split_basis_points': 3300})
        response = protected.post('/api/workspace/layout/', data=body, content_type='application/json',
            secure=True, HTTP_X_MAIPA_LAYOUT=initial['layout_id'])
        self.assertEqual(response.status_code, 403)
        token = protected.get('/api/session/csrf/', secure=True).json()['csrf_token']
        response = protected.post('/api/workspace/layout/', data=body, content_type='application/json', secure=True,
            HTTP_ORIGIN='https://foreign.invalid', HTTP_X_CSRFTOKEN=token, HTTP_X_MAIPA_LAYOUT=initial['layout_id'])
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.state(), initial)
        Membership.objects.filter(user=self.owner).update(enabled=False)
        self.assertEqual(self.client.get('/api/workspace/layout/', secure=True).status_code, 403)
        self.assertEqual(self.save(initial, 3300).status_code, 403)
        Membership.objects.filter(user=self.owner).update(enabled=True)
        response = self.client.put('/api/workspace/layout/', data='{}', content_type='application/json', secure=True)
        self.assertEqual((response.status_code, response['Allow']), (405, 'GET, POST'))
        self.assertEqual(self.state(), initial)
