"""A long conversation keeps working: older messages move to a stored archive instead of blocking new ones."""
import json
import uuid
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from backend.maipa.agent_accounts.models import AgentAccount, Conversation, ConversationArchive
from backend.maipa.storage import prepare_directory, prepare_files
from backend.maipa.workspace.models import Installation, Membership


class ConversationArchiveTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='maipa-archive-store-')
        self.addCleanup(temporary.cleanup)
        data = prepare_directory(Path(temporary.name).resolve() / 'install')
        prepare_files(data)
        configured = override_settings(DATA_ROOT=data)
        configured.enable()
        self.addCleanup(configured.disable)
        self.installation = Installation.objects.create()
        self.owner = get_user_model().objects.create_user(username='archive-owner')
        self.other = get_user_model().objects.create_user(username='archive-other')
        Membership.objects.create(user=self.owner, installation=self.installation, role='preparer')
        Membership.objects.create(user=self.other, installation=self.installation, role='preparer')
        AgentAccount.objects.create(owner=self.owner, provider='codex', label='Codex')
        AgentAccount.objects.create(owner=self.other, provider='codex', label='Codex')
        from backend.maipa.agent_accounts import chat
        self.api = chat
        with patch.object(chat, 'start_worker'):
            chat.send(self.owner, 0, str(uuid.uuid4()), 'Synthetic first message')

    def fill(self, count, size=20):
        """Replace the transcript with `count` finished messages, as completed turns would leave it."""
        messages = [{'id': str(uuid.uuid4()), 'role': 'user' if n % 2 == 0 else 'assistant', 'text': f'Synthetic message {n} ' + 'x' * size, 'state': 'complete'}
                    for n in range(count)]
        row = Conversation.objects.get(user=self.owner)
        Conversation.objects.filter(pk=row.pk).update(messages=messages, requests=[m['id'] for m in messages if m['role'] == 'user'],
                                                       phase='ready', revision=row.revision + 1)
        return messages

    def send(self, text='Synthetic next message', request_id=None):
        revision = Conversation.objects.get(user=self.owner).revision
        with patch.object(self.api, 'start_worker'):
            return self.api.send(self.owner, revision, request_id or str(uuid.uuid4()), text)

    def test_a_full_conversation_accepts_the_next_message_and_archives_the_oldest_in_order(self):
        before = self.fill(126)
        state = self.send('The message that used to be refused')
        self.assertEqual((len(state['messages']), state['messages'][-1]['text'], state['messages'][-1]['state'], state['archived']),
                         (61, 'The message that used to be refused', 'queued', 66))
        self.assertEqual(state['messages'][:-1], before[66:])
        saved = ConversationArchive.objects.get()
        self.assertEqual((saved.position, saved.count, saved.messages, saved.user_id), (0, 66, before[:66], self.owner.pk))
        self.assertEqual(self.api.snapshot(self.owner)['archived'], 66)
        # Nothing is lost: archive plus live transcript is every message, once, in order.
        self.assertEqual(saved.messages + state['messages'][:-1], before)

    def test_a_short_conversation_is_left_alone_and_reports_no_archive(self):
        before = self.fill(98)
        state = self.send()
        self.assertEqual((len(state['messages']), state['archived'], ConversationArchive.objects.count()), (99, 0, 0))
        self.assertEqual(state['messages'][:-1], before)
        self.assertEqual(self.api.snapshot(self.other)['messages'], [])

    def test_large_messages_are_archived_by_size_and_the_newest_are_always_kept(self):
        before = self.fill(12, size=15000)
        state = self.send()
        kept = state['messages'][:-1]
        self.assertEqual(kept, before[-len(kept):])
        self.assertTrue(2 <= len(kept) < 12)
        self.assertLessEqual(len(json.dumps(kept).encode()), 65536)
        self.assertEqual((state['archived'], ConversationArchive.objects.get().messages), (12 - len(kept), before[:12 - len(kept)]))

    def test_repeated_rollovers_append_segments_and_earlier_messages_can_be_read_back_newest_first(self):
        first = self.fill(126)
        self.send()
        row = Conversation.objects.get(user=self.owner)
        live = [dict(m, state='complete') for m in row.messages]
        more = [{'id': str(uuid.uuid4()), 'role': 'assistant', 'text': f'Synthetic later {n}', 'state': 'complete'} for n in range(45)]
        Conversation.objects.filter(pk=row.pk).update(messages=live + more, phase='ready', revision=row.revision + 1)
        state = self.send()
        self.assertEqual(state['archived'], 66 + 46)
        segments = list(ConversationArchive.objects.order_by('position'))
        self.assertEqual([(s.position, s.count) for s in segments], [(0, 66), (66, 46)])
        everything = segments[0].messages + segments[1].messages
        self.assertEqual(everything, (first + live[60:] + more)[:112])
        page = self.api.earlier(self.owner, 0)
        self.assertEqual((page['total'], len(page['messages']), page['next']), (112, 50, 50))
        self.assertEqual(page['messages'], everything[-50:])
        last = self.api.earlier(self.owner, 100)
        self.assertEqual((last['messages'], last['next']), (everything[:12], None))
        self.assertEqual(self.api.earlier(self.owner, 112), {'total': 112, 'messages': [], 'next': None})
        self.assertEqual(self.api.earlier(self.other, 0), {'total': 0, 'messages': [], 'next': None})
        for bad in (-1, '0', 1.5, None, True, 10 ** 9):
            with self.assertRaises(ValueError, msg=bad):
                self.api.earlier(self.owner, bad)

    def test_an_archived_request_is_not_run_again_and_an_unfinished_turn_is_never_archived(self):
        before = self.fill(126)
        self.send()
        with self.assertRaises(self.api.ChatConflict):
            self.send('Synthetic message 0 ' + 'x' * 20, request_id=before[0]['id'])
        row = Conversation.objects.get(user=self.owner)
        self.assertEqual(row.messages[-1]['state'], 'queued')
        self.assertEqual(ConversationArchive.objects.count(), 1)
        # While a turn is running the transcript cannot be sent to, so it cannot be archived mid-turn either.
        with self.assertRaises(self.api.ChatConflict):
            with patch.object(self.api, 'start_worker'):
                self.api.send(self.owner, row.revision, str(uuid.uuid4()), 'Second message during the turn')
        self.assertEqual(Conversation.objects.get(user=self.owner).messages, row.messages)

    def test_http_reads_earlier_messages_only_for_their_owner(self):
        self.fill(126)
        self.send()
        self.client.force_login(self.owner)
        actor = {'HTTP_X_MAIPA_AGENT_ACTOR': str(self.owner.pk)}
        response = self.client.get('/api/agent/chat/earlier/?offset=0', secure=True, **actor)
        self.assertEqual((response.status_code, response.json()), (200, self.api.earlier(self.owner, 0)))
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(self.client.get('/api/agent/chat/', secure=True, **actor).json()['archived'], 66)
        for url in ('/api/agent/chat/earlier/', '/api/agent/chat/earlier/?offset=-1', '/api/agent/chat/earlier/?offset=x', '/api/agent/chat/earlier/?offset=0&user=2'):
            self.assertEqual(self.client.get(url, secure=True, **actor).status_code, 400, url)
        self.assertEqual(self.client.get('/api/agent/chat/earlier/?offset=0', secure=True).status_code, 409)
        self.assertEqual(self.client.post('/api/agent/chat/earlier/?offset=0', secure=True, **actor).status_code, 405)
        self.client.force_login(self.other)
        foreign = self.client.get('/api/agent/chat/earlier/?offset=0', secure=True, HTTP_X_MAIPA_AGENT_ACTOR=str(self.other.pk))
        self.assertEqual((foreign.status_code, foreign.json()), (200, {'total': 0, 'messages': [], 'next': None}))
