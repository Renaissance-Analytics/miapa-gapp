"""UI composition is original-session authority, never model-selected actors or code."""
import json
from pathlib import Path
import tempfile

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from backend.maipa.agent_accounts.models import AgentAccount, SessionReference
from backend.maipa.agent_accounts.references import record_session
from backend.maipa.storage import prepare_directory, prepare_files
from backend.maipa.workspace.models import Installation, Membership

SCHEMA = {'id': 'review', 'type': 'Maipa.Stack', 'children': [
    {'id': 'comment', 'type': 'Maipa.Note', 'props': {'text': 'Synthetic unverified commentary'}},
    {'id': 'facts', 'type': 'Maipa.ClientFacts'},
]}


class UiCompositionTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='maipa-ui-composition-')
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name).resolve() / 'install'
        prepare_directory(self.data)
        prepare_files(self.data)
        configured = override_settings(DATA_ROOT=self.data)
        configured.enable()
        self.addCleanup(configured.disable)
        self.installation = Installation.objects.create()
        self.owner = get_user_model().objects.create_user(username='ui-owner')
        self.other = get_user_model().objects.create_user(username='ui-other')
        self.member = Membership.objects.create(user=self.owner, installation=self.installation, role='preparer')
        Membership.objects.create(user=self.other, installation=self.installation, role='preparer')
        self.account = AgentAccount.objects.create(owner=self.owner, provider='codex', label='Synthetic UI account')
        self.session = record_session(self.owner, str(self.account.pk), 'synthetic-private-provider-handle')

    def api(self):
        from backend.maipa.agent_accounts import ui
        return ui

    def lease(self):
        return self.api().open_ui_lease(self.owner, str(self.account.pk), self.session)

    def call(self, lease, name, arguments):
        return self.api().dispatch_ui(self.owner, str(self.account.pk), lease,
                                      {'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}})

    def snapshot(self, lease):
        return self.api().ui_snapshot(self.owner, str(self.account.pk), lease)

    def refused(self, operation):
        with self.assertRaises(self.api().UiUnavailable) as caught:
            operation()
        self.assertEqual(str(caught.exception), 'UI composition unavailable')
        self.assertIsNone(caught.exception.__cause__)

    def test_empty_session_catalog_and_revisioned_present_update_dismiss(self):
        lease = self.lease()
        self.assertEqual(self.snapshot(lease), {'id': lease, 'revision': 0, 'composition': None})
        catalog = self.call(lease, 'maipa_ui_catalog', {})
        self.assertEqual(catalog['revision'], 0)
        self.assertEqual([item['type'] for item in catalog['components']],
                         ['Maipa.Stack', 'Maipa.Note', 'Maipa.ClientFacts', 'Maipa.ClientCatalog'])
        self.assertNotIn('synthetic-private-provider-handle', json.dumps(catalog))
        presented = self.call(lease, 'maipa_ui_present', {'revision': 0, 'title': 'Review', 'schema': SCHEMA})
        self.assertEqual(presented['composition'], {'title': 'Review', 'schema': SCHEMA})
        self.assertEqual(presented['revision'], 1)
        note = {'id': 'updated-note', 'type': 'Maipa.Note', 'props': {'text': 'Updated'}}
        changed = self.call(lease, 'maipa_ui_update', {'revision': 1, 'title': 'Updated review', 'schema': note})
        self.assertEqual(changed['composition'], {'title': 'Updated review', 'schema': note})
        self.assertEqual(self.call(lease, 'maipa_ui_dismiss', {'revision': 2}),
                         {'id': lease, 'revision': 3, 'composition': None})

    def test_invalid_schema_and_stale_updates_cannot_change_controlled_state(self):
        lease = self.lease()
        for schema in (
            {'id': 'x', 'type': 'iframe', 'props': {'src': 'https://foreign.invalid'}},
            {'id': 'x', 'type': 'Maipa.ClientFacts', 'props': {'user_id': str(self.other.pk)}},
            {'id': 'x', 'type': 'Maipa.Note', 'props': {'text': 'x', 'onClick': 'execute()'}},
            {'id': 'x', 'type': 'Maipa.WorkspaceGuide'},
            {'id': 'x', 'type': 'Maipa.Stack', 'children': [{'id': 'x', 'type': 'Maipa.ClientFacts'}]},
            {'id': 'x', 'type': 'Maipa.Stack', 'children': [SCHEMA] * 17},
        ):
            self.refused(lambda: self.call(lease, 'maipa_ui_present', {'revision': 0, 'title': 'Rejected', 'schema': schema}))
            self.assertEqual(self.snapshot(lease)['revision'], 0)
            self.assertIsNone(self.snapshot(lease)['composition'])
        saved = self.call(lease, 'maipa_ui_present', {'revision': 0, 'title': 'Review', 'schema': SCHEMA})
        for name, args in (
            ('maipa_ui_update', {'revision': 0, 'title': 'Stale', 'schema': SCHEMA}),
            ('maipa_ui_dismiss', {'revision': 0}),
            ('maipa_ui_dismiss', {'revision': True}),
            ('maipa_ui_present', {'revision': 1, 'title': 'Replacement', 'schema': SCHEMA}),
            ('maipa_ui_update', {'revision': 1, 'title': 'x\x00', 'schema': SCHEMA}),
        ):
            self.refused(lambda: self.call(lease, name, args))
            self.assertEqual(self.snapshot(lease), saved)

    def test_foreign_owner_and_revoked_session_cannot_read_or_dispatch(self):
        lease = self.lease()
        self.refused(lambda: self.api().ui_snapshot(self.other, str(self.account.pk), lease))
        self.refused(lambda: self.api().dispatch_ui(self.other, str(self.account.pk), lease,
                                                  {'method': 'tools/list', 'params': {}}))
        SessionReference.objects.filter(pk=self.session).update(revoked_at='2026-10-26T00:00:00Z')
        self.refused(lambda: self.snapshot(lease))
        self.refused(lambda: self.call(lease, 'maipa_ui_catalog', {}))

    def test_account_membership_and_access_revision_are_rechecked_per_call(self):
        lease = self.lease()
        AgentAccount.objects.filter(pk=self.account.pk).update(enabled=False)
        self.refused(lambda: self.snapshot(lease))
        AgentAccount.objects.filter(pk=self.account.pk).update(enabled=True)
        Membership.objects.filter(pk=self.member.pk).update(role='viewer')
        self.refused(lambda: self.snapshot(lease))
        Membership.objects.filter(pk=self.member.pk).update(role='preparer')
        Installation.objects.filter(pk=self.installation.pk).update(access_revision=1)
        self.refused(lambda: self.snapshot(lease))

    def test_privileged_components_require_current_original_administrator_authority(self):
        lease = self.lease()
        schema = {'id': 'users', 'type': 'Maipa.AdminUsers'}
        self.refused(lambda: self.call(lease, 'maipa_ui_present', {'revision': 0, 'title': 'Users', 'schema': schema}))
        Membership.objects.filter(pk=self.member.pk).update(role='administrator')
        self.refused(lambda: self.snapshot(lease))
        admin_lease = self.lease()
        self.assertIn('Maipa.AdminUsers', [item['type'] for item in self.call(admin_lease, 'maipa_ui_catalog', {})['components']])
        shown = self.call(admin_lease, 'maipa_ui_present', {'revision': 0, 'title': 'Users', 'schema': schema})
        self.assertEqual(shown['composition']['schema'], schema)
        Membership.objects.filter(pk=self.member.pk).update(role='preparer')
        self.refused(lambda: self.snapshot(admin_lease))

    def test_tools_are_closed_and_bound_arguments_cannot_choose_principal_or_actions(self):
        lease = self.lease()
        listed = self.api().dispatch_ui(self.owner, str(self.account.pk), lease, {'method': 'tools/list', 'params': {}})
        self.assertEqual([item['name'] for item in listed['tools']],
                         ['maipa_ui_catalog', 'maipa_ui_present', 'maipa_ui_update', 'maipa_ui_dismiss'])
        for value in (
            {'method': 'terminal/create', 'params': {}},
            {'method': 'tools/list', 'params': {'actor': str(self.other.pk)}},
            {'method': 'tools/call', 'params': {'name': 'financial_post', 'arguments': {}}},
            {'method': 'tools/call', 'params': {'name': 'maipa_ui_catalog', 'arguments': {'user_id': str(self.other.pk)}}},
            {'method': 'tools/call', 'params': {'name': 'maipa_ui_catalog', 'arguments': {}, 'account': 'foreign'}},
        ):
            self.refused(lambda: self.api().dispatch_ui(self.owner, str(self.account.pk), lease, value))
        self.assertEqual(self.snapshot(lease)['revision'], 0)

    def test_http_requires_session_current_actor_and_csrf_then_shares_controlled_revision(self):
        lease = self.lease()
        url = f'/api/agent/accounts/{self.account.pk}/ui/{lease}/'
        client = Client(enforce_csrf_checks=True)
        anonymous = client.get(url, secure=True)
        self.assertEqual(anonymous.status_code, 401)
        self.assertIn('no-store', anonymous['Cache-Control'])
        client.force_login(self.owner)
        self.assertEqual(client.get(url, secure=True).status_code, 409)
        headers = {'HTTP_X_MAIPA_AGENT_ACTOR': str(self.owner.pk)}
        self.assertEqual(client.get(url, secure=True, **headers).json()['composition'], None)
        body = {'method': 'tools/call', 'params': {'name': 'maipa_ui_present', 'arguments': {'revision': 0, 'title': 'Review', 'schema': SCHEMA}}}
        self.assertEqual(client.post(url, body, content_type='application/json', secure=True, **headers).status_code, 403)
        csrf = client.get('/api/session/csrf/', secure=True).json()['csrf_token']
        response = client.post(url, body, content_type='application/json', secure=True,
                               HTTP_ORIGIN='https://testserver', HTTP_X_CSRFTOKEN=csrf, **headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.get(url, secure=True, **headers).json(), response.json())
        client.force_login(self.other)
        self.assertEqual(client.get(url, secure=True, HTTP_X_MAIPA_AGENT_ACTOR=str(self.other.pk)).status_code, 404)
