"""Real app consumer with a finite deterministic ACP peer; no real-model claim."""
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TransactionTestCase, override_settings

from backend.maipa.acp import Scope
from backend.maipa.agent_accounts import chat, chat_runtime
from backend.maipa.agent_accounts.models import AgentAccount, SessionReference
from backend.maipa.agent_accounts.ui import dispatch_ui
from backend.maipa.storage import prepare_directory, prepare_files
from backend.maipa.workspace.models import Installation, Membership


class AgentChatRuntimeTests(TransactionTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='maipa-chat-runtime-')
        self.root = Path(self.temporary.name).resolve()
        data = prepare_directory(self.root / 'install')
        prepare_files(data)
        self.configured = override_settings(DATA_ROOT=data)
        self.configured.enable()
        install = Installation.objects.create()
        self.owner = get_user_model().objects.create_user(username='runtime-owner')
        Membership.objects.create(user=self.owner, installation=install, role='preparer')
        self.account = AgentAccount.objects.create(owner=self.owner, provider='codex', label='Synthetic ACP')
        self.client.force_login(self.owner)
        self.started_workers = []
        root = self.root
        def prepare(worker):
            self.started_workers.append(worker)
            worker.check()
            workspace = root / 'workspace'
            workspace.mkdir(exist_ok=True)
            scope = Scope(str(worker.owner), str(worker.account_id))
            worker.lease = SimpleNamespace(scope=scope, revalidate=lambda: None)
            return SimpleNamespace(command=(sys.executable, str(Path(__file__).parent/'fixtures/agent_chat_peer.py')),
                workspace=workspace, profile={'user_id': scope.user_id, 'account_id': scope.account_id, 'workspace': str(workspace)}), None
        self.prepared = patch.object(chat_runtime._Worker, 'prepare', prepare)
        self.prepared.start()

    def tearDown(self):
        with chat_runtime._lock:
            workers = list(chat_runtime._workers.values())
            for worker in workers: worker.stopping.set()
        for worker in workers:
            worker.thread.join(timeout=10)
            self.assertFalse(worker.thread.is_alive(), 'Owned ACP consumer must stop')
            self.assertIsNotNone(worker.client.returncode)
        self.prepared.stop()
        self.configured.disable()
        self.temporary.cleanup()
        super().tearDown()

    def state(self):
        response = self.client.get('/api/agent/chat/', HTTP_X_MAIPA_AGENT_ACTOR=str(self.owner.pk))
        self.assertEqual(response.status_code, 200)
        return response.json()

    def until(self, condition):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            value = self.state()
            if condition(value): return value
            time.sleep(.03)
        phases = [{'stage': worker.stage, 'kind': worker.failure_kind, 'exited': worker.client.returncode is not None if worker.client else None} for worker in self.started_workers]
        self.fail('Owned deterministic ACP checkpoint unavailable: ' + str(phases))

    def send(self, text):
        state = self.state()
        response = self.client.post('/api/agent/chat/', {'action': 'send', 'revision': state['revision'],
            'request_id': str(uuid.uuid4()), 'text': text}, content_type='application/json',
            HTTP_X_MAIPA_AGENT_ACTOR=str(self.owner.pk))
        self.assertEqual(response.status_code, 200)
        return self.until(lambda s: s['phase'] == 'ready' and s['messages'][-1]['role'] == 'assistant'
                          and s['messages'][-1]['state'] == 'complete')

    def test_http_turn_streams_persists_and_model_tools_share_present_update_dismiss(self):
        import copy
        checkpoints = []
        original_update = chat.worker_update
        def checkpoint(*args, **kwargs):
            checkpoints.append(copy.deepcopy(kwargs))
            return original_update(*args, **kwargs)
        with patch.object(chat, 'worker_update', side_effect=checkpoint):
            initial = self.send('Present a note')
            streamed = [value['messages'][-1]['text'] for value in checkpoints
                        if 'messages' in value and value['messages'][-1]['state'] == 'streaming']
        self.assertTrue(any(text and text != initial['messages'][-1]['text'] for text in streamed))
        self.assertEqual(initial['messages'][-1]['text'], 'Synthetic agent reply. Shared UI is synchronized.')
        ui = initial['ui']
        self.assertEqual(ui['composition']['schema']['id'], 'shared-root')
        self.assertEqual(ui['composition']['schema']['props']['text'], 'Synthetic shared note')
        # A human writes the same controlled revision before the next agent turn.
        dispatch_ui(self.owner, ui['account_id'], ui['id'], {'method': 'tools/call', 'params': {
            'name': 'maipa_ui_update', 'arguments': {'revision': ui['revision'], 'title': 'Human title',
                'schema': {'id': 'shared-root', 'type': 'Maipa.Note', 'props': {'text': 'Human change'}}}}})
        updated = self.send('Update the note')
        self.assertEqual(updated['ui']['id'], ui['id'])
        self.assertEqual(updated['ui']['revision'], ui['revision'] + 2)
        self.assertEqual(updated['ui']['composition']['schema']['props']['text'], 'Synthetic updated note')
        dismissed = self.send('Dismiss the contextual panel')
        self.assertIsNone(dismissed['ui']['composition'])
        self.assertEqual(len(dismissed['messages']), 6)
        self.assertEqual(self.state()['messages'], dismissed['messages'])

    def test_logout_revokes_live_worker_and_private_ui_but_keeps_owned_transcript(self):
        initial = self.send('Present client facts')
        with chat_runtime._lock: worker = chat_runtime._workers[self.owner.pk]
        self.client.logout()
        worker.thread.join(timeout=10)
        self.assertFalse(worker.thread.is_alive())
        self.assertIsNotNone(worker.client.returncode)
        self.assertTrue(SessionReference.objects.filter(pk=worker.reference, revoked_at__isnull=False).exists())
        self.assertEqual(self.client.get('/api/agent/chat/', HTTP_X_MAIPA_AGENT_ACTOR=str(self.owner.pk)).status_code, 401)
        self.client.force_login(self.owner)
        restored = self.state()
        self.assertIsNone(restored['ui'])
        self.assertEqual(restored['messages'], initial['messages'])

    def test_browser_write_requires_csrf_before_starting_any_provider(self):
        secured = Client(enforce_csrf_checks=True)
        secured.force_login(self.owner)
        response = secured.post('/api/agent/chat/', {'action': 'connect', 'revision': 0},
            content_type='application/json', HTTP_X_MAIPA_AGENT_ACTOR=str(self.owner.pk))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.state()['phase'], 'offline')
        with chat_runtime._lock: self.assertNotIn(self.owner.pk, chat_runtime._workers)

    def test_access_epoch_reauthorizes_same_actor_but_role_change_stops_worker(self):
        from django.db.models import F
        from backend.maipa.agent_accounts.ui import UiUnavailable, ui_snapshot
        initial = self.send('Present a note')
        with chat_runtime._lock: worker = chat_runtime._workers[self.owner.pk]
        Installation.objects.update(access_revision=F('access_revision') + 1)
        fresh = self.until(lambda s: s['ui'] is not None and s['ui']['id'] != initial['ui']['id'])
        with self.assertRaises(UiUnavailable):
            ui_snapshot(self.owner, initial['ui']['account_id'], initial['ui']['id'])
        self.assertEqual(fresh['ui']['composition'], initial['ui']['composition'])
        Membership.objects.filter(user=self.owner).update(role='viewer')
        worker.thread.join(timeout=10)
        self.assertFalse(worker.thread.is_alive())
        self.assertEqual(self.client.get('/api/agent/chat/', HTTP_X_MAIPA_AGENT_ACTOR=str(self.owner.pk)).status_code, 403)

    def test_access_epoch_between_actor_check_and_snapshot_does_not_remove_shared_panel(self):
        from django.db.models import F
        initial = self.send('Present a note')
        snapshot = chat.ui_snapshot
        advanced = False
        def concurrent_epoch(*args):
            nonlocal advanced
            if not advanced:
                advanced = True
                Installation.objects.update(access_revision=F('access_revision') + 1)
            return snapshot(*args)
        with patch.object(chat, 'ui_snapshot', side_effect=concurrent_epoch):
            current = self.state()
        self.assertIsNotNone(current['ui'], 'A single unrelated access epoch must not unmount the shared panel')
        self.assertNotEqual(current['ui']['id'], initial['ui']['id'])
        self.assertEqual(current['ui']['composition'], initial['ui']['composition'])

    def test_role_revoked_between_actor_check_and_snapshot_does_not_return_private_ui(self):
        self.send('Present a note')
        snapshot = chat.ui_snapshot
        def revoked_role(*args):
            Membership.objects.filter(user=self.owner).update(role='viewer')
            return snapshot(*args)
        with patch.object(chat, 'ui_snapshot', side_effect=revoked_role):
            with self.assertRaises(chat.ChatUnavailable):
                chat.snapshot(self.owner)

    def test_epoch_rotating_while_minting_ui_reauthorizes_without_restarting_worker(self):
        from django.db.models import F
        initial = self.send('Present a note')
        worker = self.started_workers[-1]
        original = chat_runtime.bind_ui_handler
        advanced = False
        def concurrent_epoch(*args):
            nonlocal advanced
            if not advanced:
                advanced = True
                Installation.objects.update(access_revision=F('access_revision') + 1)
            return original(*args)
        Installation.objects.update(access_revision=F('access_revision') + 1)
        with patch.object(chat_runtime, 'bind_ui_handler', side_effect=concurrent_epoch):
            current = self.state()
        self.assertTrue(advanced)
        self.assertEqual(current['ui']['composition'], initial['ui']['composition'])
        self.assertNotEqual(current['ui']['id'], initial['ui']['id'])
        self.assertFalse(worker.stopping.is_set())
        self.assertIsNone(worker.failure_kind)

    def test_role_revoked_while_minting_ui_is_not_reauthorized(self):
        from django.db.models import F
        self.send('Present a note')
        original = chat_runtime.bind_ui_handler
        def revoked_role(*args):
            Membership.objects.filter(user=self.owner).update(role='viewer')
            return original(*args)
        Installation.objects.update(access_revision=F('access_revision') + 1)
        with patch.object(chat_runtime, 'bind_ui_handler', side_effect=revoked_role):
            with self.assertRaises(chat.ChatUnavailable):
                chat.snapshot(self.owner)

    def test_cancel_preserves_partial_reply_and_acknowledged_session_can_continue(self):
        response = self.client.post('/api/agent/chat/', {'action': 'send', 'revision': 0,
            'request_id': str(uuid.uuid4()), 'text': 'Present a note and wait for cancellation'},
            content_type='application/json', HTTP_X_MAIPA_AGENT_ACTOR=str(self.owner.pk))
        self.assertEqual(response.status_code, 200)
        partial = self.until(lambda s: s['phase'] == 'thinking' and s['messages'][-1]['role'] == 'assistant'
                             and bool(s['messages'][-1]['text']))
        identity = partial['ui']['id']
        response = self.client.post('/api/agent/chat/', {'action': 'cancel'}, content_type='application/json',
                                    HTTP_X_MAIPA_AGENT_ACTOR=str(self.owner.pk))
        self.assertEqual(response.status_code, 200)
        stopped = self.until(lambda s: s['phase'] == 'ready' and s['messages'][-1]['state'] == 'interrupted')
        self.assertEqual(stopped['messages'][-1]['text'], partial['messages'][-1]['text'])
        late = self.client.post('/api/agent/chat/', {'action': 'cancel'}, content_type='application/json',
                               HTTP_X_MAIPA_AGENT_ACTOR=str(self.owner.pk))
        self.assertEqual(late.status_code, 200)
        continued = self.send('Update the note')
        self.assertEqual(continued['ui']['id'], identity)
        self.assertEqual(len(continued['messages']), 4)

    def test_brief_concurrent_reader_does_not_abort_startup_checkpoint(self):
        from django.db import transaction
        from backend.maipa.agent_accounts.models import Conversation
        # A normal concurrent request may hold a short read transaction while a
        # new worker publishes its acknowledged session. Use the actual DB lock.
        response = self.client.post('/api/agent/chat/', {'action': 'send', 'revision': 0,
            'request_id': str(uuid.uuid4()), 'text': 'Present a note'}, content_type='application/json',
            HTTP_X_MAIPA_AGENT_ACTOR=str(self.owner.pk))
        self.assertEqual(response.status_code, 200)
        with transaction.atomic():
            self.assertEqual(Conversation.objects.get(account=self.account).messages[0]['text'], 'Present a note')
            time.sleep(.2)
        complete = self.until(lambda s: s['phase'] == 'ready' and s['messages'][-1]['role'] == 'assistant'
                              and s['messages'][-1]['state'] == 'complete')
        self.assertEqual(complete['ui']['composition']['schema']['type'], 'Maipa.Note')
