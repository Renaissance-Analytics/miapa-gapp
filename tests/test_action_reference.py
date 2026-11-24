"""The agent must be told every approval operation's exact fields, in text short enough that a provider delivers it whole."""
import asyncio
import re
import uuid

from django.test import TestCase

from backend.maipa.agent_accounts import action_reference, chat_runtime, ui
from backend.maipa.agent_accounts.form_tools import public_form_tools
from backend.maipa.agent_accounts.workflow_tools import public_workflow_tools
from backend.maipa.approvals import services as approvals
from backend.maipa.approvals.models import ActionRequest
from backend.maipa.ledger.tools import tool_definitions as ledger_tools
from tests.test_agent_approvals import AgentApprovalToolTests


def named(signature):
    """('required fields', 'optional fields') of a signature such as {a,b[,c][,d,e]}."""
    inner = signature[1:-1]
    optional = set(','.join(re.findall(r'\[,?([^\]]*)\]', inner)).split(',')) - {''}
    return set(re.sub(r'\[[^\]]*\]', '', inner).split(',')) - {''}, optional


class ActionReferenceTests(TestCase):
    def test_every_operation_is_described_with_exactly_the_fields_the_server_requires(self):
        self.assertEqual(sorted(name for name, _, _ in action_reference.OPERATIONS), sorted(approvals.OPERATIONS))
        for name, signature, _ in action_reference.OPERATIONS:
            required, optional = named(signature)
            self.assertEqual((required, optional), (set(approvals.OPERATIONS[name][0]), set(approvals.OPTIONAL.get(name, ()))), name)
        tool = next(item for item in ui._tools() if item['name'] == 'maipa_action_request')
        self.assertEqual(sorted(tool['inputSchema']['properties']['operation']['enum']), sorted(approvals.OPERATIONS))
        for name, signature, detail in action_reference.OPERATIONS:
            self.assertIn(f'{name} {signature}', tool['description'], name)
            self.assertIn(f'{name} {signature}' + (f': {detail}.' if detail else '.'), chat_runtime.INSTRUCTIONS, name)
        self.assertIn('You cannot approve.', tool['description'])

    def test_no_tool_description_is_long_enough_to_be_cut_off(self):
        self.assertLessEqual(action_reference.DESCRIPTION_LIMIT, 3500)
        tools = [*ui._tools(), *ledger_tools(), *asyncio.run(public_form_tools()), *asyncio.run(public_workflow_tools())]
        self.assertGreaterEqual(len(tools), 40)
        for tool in tools:
            self.assertLessEqual(len(tool['description']), action_reference.DESCRIPTION_LIMIT, tool['name'])
        # A long description would have hidden the last operations; the old text was over 5,000 characters.
        self.assertLess(len(action_reference.description()), 2500)

    def test_a_request_with_the_wrong_fields_is_refused_and_told_the_expected_ones(self):
        AgentApprovalToolTests.setUp(self)
        book = str(uuid.uuid4())
        ask = lambda arguments: AgentApprovalToolTests.call(self, 'maipa_action_request', arguments)
        self.assertEqual(ask({'operation': 'report.release', 'summary': 'Release statements', 'payload': {'book_id': book, 'from': '2026-01-01', 'through': '2026-06-30'}}),
                         {'requested': False, 'reason': 'invalid_approval_request', 'expected_payload': '{book_id,from,to,title}'})
        self.assertEqual(ask({'operation': 'period.close', 'summary': 'Close', 'payload': {'book_id': book}})['expected_payload'], '{book_id,through}')
        # Other refusals, and unknown operations, say only their reason.
        self.assertEqual(ask({'operation': 'report.publish', 'summary': 'Publish', 'payload': {}}), {'requested': False, 'reason': 'approval_operation_unsupported'})
        refused = ask({'operation': 'period.close', 'summary': 'Close', 'payload': {'book_id': book, 'through': '2026-06-30'}})
        self.assertEqual((refused['requested'], 'expected_payload' in refused), (False, False))
        self.assertEqual(ActionRequest.objects.count(), 0)
