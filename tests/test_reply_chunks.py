"""Reply text streamed around tool use is separated, and nothing but text reaches the chat."""
from types import SimpleNamespace
import unittest

from backend.maipa.agent_accounts.chat_runtime import _Worker


class ReplyChunkTests(unittest.TestCase):
    def worker(self, updates):
        client = SimpleNamespace(drain_updates=lambda scope, session: updates.pop(0) if updates else [])
        return SimpleNamespace(client=client, lease=SimpleNamespace(scope=None))

    def text(self, value):
        return {'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': value}}

    def test_text_after_tool_use_starts_a_new_paragraph_and_tool_content_is_never_shown(self):
        worker = self.worker([[self.text('One left, then the review step.')],
                              [{'sessionUpdate': 'tool_call', 'content': {'type': 'text', 'text': 'PRIVATE TOOL ARGUMENTS'}}],
                              [self.text("I've recorded all your answers."), self.text(' Ready to review.')]])
        response = {'id': 'm', 'role': 'assistant', 'text': '', 'state': 'streaming'}
        self.assertTrue(_Worker.chunks(worker, 'session', response))
        self.assertFalse(_Worker.chunks(worker, 'session', response))
        self.assertTrue(_Worker.chunks(worker, 'session', response))
        self.assertEqual(response['text'], "One left, then the review step.\n\nI've recorded all your answers. Ready to review.")
        self.assertEqual(set(response), {'id', 'role', 'text', 'state'})

    def test_no_separator_is_added_at_the_start_or_between_plain_text_chunks(self):
        worker = self.worker([[{'sessionUpdate': 'tool_call'}], [self.text('Hello'), self.text(' there')],
                              [{'sessionUpdate': 'tool_call'}], [self.text('\n\nNext')]])
        response = {'id': 'm', 'role': 'assistant', 'text': '', 'state': 'streaming'}
        for _ in range(4):
            _Worker.chunks(worker, 'session', response)
        self.assertEqual(response['text'], 'Hello there\n\nNext')
