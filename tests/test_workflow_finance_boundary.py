"""Workflows cannot reach financial records: no admitted node kind writes them, and a run or its approval changes none."""
from pathlib import Path
import re
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from backend.maipa.approvals import services as approvals
from backend.maipa.approvals.models import ActionRequest
from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.ledger import services as ledger
from backend.maipa.ledger.models import Journal, LedgerEvent, SourceImport
from backend.maipa.workflows import schema, services as flows
from backend.maipa.workflows.models import WorkflowRun
from backend.maipa.workspace.models import Installation, Membership

PERIOD = {'currency': 'USD', 'period_start': '2026-01-01', 'period_end': '2026-12-31'}


def graph(kind='transform', expression='Post journal {{ in.journal_id }} for {{ in.amount }}'):
    return {'version': 1, 'graph': {'nodes': [
        {'id': 'start', 'kind': 'manual_trigger', 'config': {}, 'position': {'x': 0, 'y': 0}},
        {'id': 'step', 'kind': kind, 'config': {'expression': expression} if kind == 'transform' else {}, 'position': {'x': 160, 'y': 0}},
        {'id': 'result', 'kind': 'output', 'config': {}, 'position': {'x': 320, 'y': 0}},
    ], 'edges': [{'id': 'a', 'source': 'start', 'target': 'step'}, {'id': 'b', 'source': 'step', 'target': 'result'}]}}


class WorkflowFinanceBoundaryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        install = Installation.objects.create()
        cls.owner = get_user_model().objects.create_user(username='boundary-owner')
        Membership.objects.create(user=cls.owner, installation=install, role='preparer')
        cls.entity = Entity.objects.create(engagement=Engagement.objects.create(
            owner=cls.owner, installation=install, name='Synthetic boundary client'), name='Synthetic boundary business')

    def setUp(self):
        self.book = ledger.create_book(self.owner, self.entity.pk, PERIOD)
        accounts = {a['code']: a['id'] for a in self.book['accounts']}
        self.draft = ledger.propose_journal(self.owner, self.book['id'], {
            'operation_id': str(uuid.uuid4()), 'expected_revision': 0, 'date': '2026-03-05', 'description': 'Synthetic rent',
            'source': {'kind': 'manual', 'reference': 'synthetic-rent'}, 'evidence': ['synthetic-rent'],
            'lines': [{'account_id': accounts['6200'], 'debit': '900', 'credit': '0'}, {'account_id': accounts['1000'], 'debit': '0', 'credit': '900'}]})
        self.request = approvals.request_action(self.owner, {'operation': 'journal.post', 'summary': 'Post the rent entry',
            'payload': {'book_id': self.book['id'], 'journal_id': self.draft['id'], 'payload_hash': self.draft['payload_hash']}})

    def financial_state(self):
        return (ledger.read_book(self.owner, self.book['id'])['revision'], sorted(Journal.objects.values_list('state', flat=True)),
                LedgerEvent.objects.count(), SourceImport.objects.count(), sorted(ActionRequest.objects.values_list('state', flat=True)),
                ledger.journal_preview(self.owner, self.book['id'])['posted_debit'])

    def test_the_admitted_node_kinds_are_exactly_the_five_reviewed_ones_and_the_workflow_code_never_touches_finance(self):
        self.assertEqual(schema.KINDS, ('manual_trigger', 'transform', 'output', 'human_approval', 'user_input'))
        root = Path(schema.__file__).resolve().parent
        sources = sorted(path for path in root.glob('*.py'))
        self.assertGreaterEqual(len(sources), 10)
        for path in sources:
            text = path.read_text(encoding='utf-8')
            self.assertIsNone(re.search(r'^\s*(?:from|import)\s+[\w.]*\b(?:ledger|approvals)\b', text, re.MULTILINE), path.name)
            self.assertIsNone(re.search(r'\b(?:propose_journal|confirm_own_journal|import_statement|reverse_journal|request_action|decide)\s*\(', text), path.name)

    def test_a_completed_run_that_names_an_entry_changes_no_books_and_decides_no_financial_request(self):
        before = self.financial_state()
        self.assertEqual(before, (1, ['draft'], 1, 0, ['pending'], '0.0000'))
        saved = flows.save_definition(self.owner, 'Synthetic posting look-alike', graph(), entity_id=str(self.entity.pk))
        run = flows.dispatch(self.owner, saved['id'], saved['revision'],
                             {'journal_id': self.draft['id'], 'amount': '900', 'approve': True, 'confirm': True}, 'boundary-dispatch')
        for _ in range(3):
            run = flows.advance(self.owner, run['id'], run['step'])
        self.assertEqual((run['status'], run['outputs']['result']), ('completed', f"Post journal {self.draft['id']} for 900"))
        self.assertEqual((WorkflowRun.objects.count(), self.financial_state()), (1, before))
        done = approvals.decide(self.owner, self.request['id'], {'decision': 'approve', 'payload_hash': self.request['payload_hash']})
        self.assertEqual((done['state'], self.financial_state()[1], self.financial_state()[5]), ('approved', ['posted'], '900.0000'))

    def test_graphs_that_name_financial_or_effectful_node_kinds_are_refused_and_nothing_is_saved(self):
        before = self.financial_state()
        for kind in ('journal.post', 'maipa_journal_propose', 'maipa_action_request', 'statement.import', 'api_request', 'webhook_out',
                     'tool_use', 'llm_call', 'data_store', 'memory_store', 'notify', 'subflow', 'terminal_run'):
            with self.subTest(kind=kind), self.assertRaises(flows.InvalidWorkflow):
                flows.save_definition(self.owner, 'Synthetic refused', graph(kind), entity_id=str(self.entity.pk))
        self.assertEqual((WorkflowRun.objects.count(), self.financial_state()), (0, before))
