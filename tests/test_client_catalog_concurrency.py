"""Two real authenticated requests contend for the same original catalog revision."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import Client, TransactionTestCase

from backend.maipa.intake.models import ClientCatalogEvent, Engagement, Entity
from backend.maipa.workspace.models import Installation, Membership


class ClientCatalogConcurrencyTests(TransactionTestCase):
    def test_two_real_clients_commit_at_most_one_create_and_one_audit_revision(self):
        installation = Installation.objects.create()
        owner = get_user_model().objects.create_user(username='catalog-concurrent')
        Membership.objects.create(installation=installation, user=owner, role='preparer')
        clients = [Client(enforce_csrf_checks=True), Client(enforce_csrf_checks=True)]
        for client in clients:
            client.force_login(owner)
        previous = clients[0].get('/api/intake/catalog/', secure=True).json()
        tokens = [client.get('/api/session/csrf/', secure=True).json()['csrf_token'] for client in clients]
        barrier = threading.Barrier(2, timeout=10)
        def run(index):
            connections.close_all()
            try:
                barrier.wait()
                body = {'action': 'create_engagement', 'name': f'Synthetic writer {index}',
                    'entity_name': f'Synthetic entity {index}', 'identity': previous['identity'],
                    'installation_id': previous['installation_id'], 'expected_revision': previous['revision']}
                response = clients[index].post('/api/intake/catalog/', data=json.dumps(body), content_type='application/json',
                    secure=True, HTTP_X_CSRFTOKEN=tokens[index], HTTP_ORIGIN='https://testserver',
                    HTTP_X_MAIPA_CLIENT_ACTOR=str(owner.pk))
                return response.status_code, response.json()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(run, (0, 1)))
        self.assertEqual(sum(status == 201 for status, _ in outcomes), 1)
        loser = next((status, value) for status, value in outcomes if status != 201)
        self.assertIn(loser[0], (409, 503))
        self.assertIn(loser[1], ({'error': {'code': 'revision_conflict'}}, {'error': {'code': 'client_catalog_unavailable'}}))
        self.assertEqual((Engagement.objects.count(), Entity.objects.count(), ClientCatalogEvent.objects.count()), (1, 1, 1))
        current = clients[0].get('/api/intake/catalog/', secure=True).json()
        self.assertEqual(current['revision'], previous['revision']+1)
        self.assertEqual(len(current['events']), 1)
        self.assertEqual(current['actor_id'], str(owner.pk))
