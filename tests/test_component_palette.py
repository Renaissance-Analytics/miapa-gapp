"""Independent fine-grained palette acceptance; no provider or financial fixtures."""
from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.maipa.agent_accounts.ui import validate_schema
from backend.maipa.workspace.models import Installation, Membership

PALETTE = {'id': 'client-overview', 'type': 'Maipa.Stack', 'children': [
    {'id': 'overview-title', 'type': 'Maipa.Heading', 'props': {'text': 'Client overview'}},
    {'id': 'review-guidance', 'type': 'Maipa.Callout', 'props': {'text': 'Review current client setup.', 'tone': 'attention'}},
    {'id': 'current-clients', 'type': 'Maipa.ClientTable', 'props': {'columns': ['name', 'entity_count']}},
]}


class ComponentPaletteTests(TestCase):
    def test_registered_palette_accepts_detached_stable_definitions_for_both_authorized_roles(self):
        for role in ('administrator', 'preparer'):
            accepted = validate_schema(PALETTE, role)
            self.assertEqual(accepted, PALETTE)
            accepted['children'][2]['props']['columns'].reverse()
            self.assertEqual(PALETTE['children'][2]['props']['columns'], ['name', 'entity_count'])

    def test_palette_rejects_record_snapshots_selectors_actions_and_invalid_content(self):
        cases = [
            ('Maipa.Heading', {'text': 'x', 'as': 'script'}),
            ('Maipa.Heading', {'text': 'x' * 121}),
            ('Maipa.Callout', {'text': 'x', 'tone': 'success'}),
            ('Maipa.Callout', {'text': 'x\x00', 'tone': 'info'}),
            ('Maipa.Callout', {'text': 'x', 'tone': 'info', 'onDismiss': 'execute()'}),
            ('Maipa.ClientTable', {'columns': ['name'], 'rows': [{'name': 'Untrusted snapshot'}]}),
            ('Maipa.ClientTable', {'columns': ['name'], 'user_id': '2'}),
            ('Maipa.ClientTable', {'columns': ['name'], 'url': '/foreign'}),
            ('Maipa.ClientTable', {'columns': ['name', 'name']}),
            ('Maipa.ClientTable', {'columns': []}),
            ('Maipa.ClientTable', {'columns': ['private_balance']}),
        ]
        for kind, props in cases:
            with self.subTest(kind=kind, props=props), self.assertRaises(ValueError):
                validate_schema({'id': 'unsafe', 'type': kind, 'props': props}, 'preparer')

    def test_personal_pin_persists_only_the_column_definition_and_revalidates_reopen(self):
        user = get_user_model().objects.create_user(username='palette-owner')
        install = Installation.objects.create()
        member = Membership.objects.create(user=user, installation=install, role='preparer')
        self.client.force_login(user)
        url = '/api/workspace/views/'
        initial = self.client.get(url, secure=True).json()
        saved = self.client.post(url, {'action': 'create', 'expected_revision': initial['revision'],
            'title': 'Client overview', 'schema': PALETTE}, content_type='application/json', secure=True,
            HTTP_X_MAIPA_VIEWS=initial['views_id'])
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()['views'][0]['schema'], PALETTE)
        self.client.logout()
        self.client.force_login(user)
        self.assertEqual(self.client.get(url, secure=True).json(), saved.json())
        member.role = 'viewer'; member.save()
        self.assertEqual(self.client.get(url, secure=True).status_code, 403)
