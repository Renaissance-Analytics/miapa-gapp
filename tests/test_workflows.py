"""Independent outcomes for the real Fancy Flow engine and durable host store."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
from threading import Barrier
from unittest.mock import patch
import uuid

from django.contrib.auth import get_user_model
from django.db import close_old_connections, transaction
from django.test import TestCase, TransactionTestCase

from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.workspace.models import Installation, Membership
from backend.maipa.workflows import services as flows
from backend.maipa.workflows.models import Definition, DefinitionVersion, WorkflowRun, NodeCheckpoint
from backend.maipa.workflows.store import DjangoClaimStore


def graph(expression='Ready {{ in.name }}'):
    return {'version': 1, 'graph': {'nodes': [
        {'id': 'start', 'kind': 'manual_trigger', 'config': {}, 'position': {'x': 0, 'y': 0}},
        {'id': 'format', 'kind': 'transform', 'config': {'expression': expression}, 'position': {'x': 160, 'y': 0}},
        {'id': 'result', 'kind': 'output', 'config': {}, 'position': {'x': 320, 'y': 0}},
    ], 'edges': [{'id': 'a', 'source': 'start', 'target': 'format'},
                 {'id': 'b', 'source': 'format', 'target': 'result'}]}}


def fixture():
    install = Installation.objects.create()
    owner = get_user_model().objects.create_user(username='flow-owner')
    other = get_user_model().objects.create_user(username='flow-other')
    member = Membership.objects.create(user=owner, installation=install, role='preparer')
    Membership.objects.create(user=other, installation=install, role='administrator')
    engagement = Engagement.objects.create(owner=owner, installation=install, name='Synthetic workflow client')
    entity = Entity.objects.create(engagement=engagement, name='Synthetic workflow entity')
    return install, owner, other, member, engagement, entity


_DEFAULT = object()


class WorkflowTests(TestCase):
    def setUp(self):
        self.install, self.owner, self.other, self.member, self.engagement, self.entity = fixture()

    def save(self, schema=_DEFAULT, **kwargs):
        return flows.save_definition(self.owner, 'Owned workflow', graph() if schema is _DEFAULT else schema,
                                     entity_id=str(self.entity.pk), **kwargs)

    def launch(self, saved=None, seed=None, key='owned-dispatch'):
        saved = saved or self.save()
        return flows.dispatch(self.owner, saved['id'], saved['revision'],
                              {'name': 'Ada'} if seed is None else seed, key)

    def finish(self, run):
        for _ in range(3):
            run = flows.advance(self.owner, run['id'], run['step'])
        return run

    def test_versioned_save_rejects_stale_edit_and_retains_original_schema(self):
        original = graph(); saved = self.save(original); original['graph']['nodes'].clear()
        revised = self.save(graph('Updated {{ in.name }}'), definition_id=saved['id'], expected_revision=1)
        with self.assertRaises(flows.Conflict):
            self.save(graph('Stale'), definition_id=saved['id'], expected_revision=1)
        self.assertEqual(revised['revision'], 2)
        versions = list(DefinitionVersion.objects.order_by('revision'))
        self.assertEqual([v.schema['graph']['nodes'][1]['config']['expression'] for v in versions],
                         ['Ready {{ in.name }}', 'Updated {{ in.name }}'])
        with self.assertRaises(ValueError): versions[0].save()
        self.assertEqual(Definition.objects.count(), 1)

    def test_refused_empty_unknown_and_effectful_graphs_never_persist(self):
        invalid = [None, [], {}, {'version': 2, 'graph': graph()['graph']}, {'version': True, 'graph': graph()['graph']},
                   {'version': 1, 'graph': {'nodes': [], 'edges': []}}]
        for kind in ('api_request', '@particle-academy/terminal_run', '@fancy/llm_call',
                     'data_store', 'human_approval', 'subflow', 'made_up'):
            value = graph(); value['graph']['nodes'][1]['kind'] = kind; invalid.append(value)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(flows.InvalidWorkflow): self.save(value)
        self.assertEqual((Definition.objects.count(), DefinitionVersion.objects.count()), (0, 0))

    def test_invalid_ports_config_cycles_and_duplicate_handles_are_rejected(self):
        mutations = [lambda g: g['graph']['edges'][0].update(source='absent'),
                     lambda g: g['graph']['edges'][0].update(sourceHandle='wrong-port'),
                     lambda g: g['graph']['nodes'][1]['config'].update(expression=''),
                     lambda g: g['graph']['nodes'][1]['config'].update(executable='hidden'),
                     lambda g: g['graph']['nodes'][1].update(id='start'),
                     lambda g: g['graph']['edges'][1].update(id='a'),
                     lambda g: g['graph']['edges'].append({'id':'cycle','source':'format','target':'format'})]
        for mutate in mutations:
            value = graph(); mutate(value)
            with self.subTest(schema=value), self.assertRaises(flows.InvalidWorkflow): self.save(value)
        self.assertFalse(Definition.objects.exists())

    def test_real_engine_persists_independently_expected_outputs_and_ports(self):
        run = self.finish(self.launch())
        self.assertEqual((run['status'], run['step'], run['error']), ('completed', 3, None))
        self.assertEqual(run['outputs'], {'start': {'name': 'Ada'}, 'format': 'Ready Ada', 'result': 'Ready Ada'})
        rows = {r.node_id:r for r in NodeCheckpoint.objects.all()}
        self.assertEqual({key:row.attempts for key,row in rows.items()}, {'start':1,'format':1,'result':1})
        self.assertEqual({key:row.ports for key,row in rows.items()}, {'start':['out'],'format':['out'],'result':[]})
        self.assertEqual(run['runtime'], 'fancy-flow/0.30.1')
        self.assertEqual((Engagement.objects.count(), Entity.objects.count()), (1, 1))

    def test_actual_typescript_export_runs_in_python_without_dropping_canvas_or_ports(self):
        schema=json.loads((Path(__file__).parent/'fixtures/flow-ts081.json').read_text(encoding='utf-8'))
        saved=self.save(schema)
        self.assertEqual(saved['schema'],schema)
        run=self.finish(self.launch(saved))
        self.assertEqual(run['outputs']['answer'],'Hello Ada')
        self.assertEqual(run['nodes']['answer']['ports'],[])
        self.assertEqual(flows.get_definition(self.owner,saved['id'])['schema']['view']['viewport'],{'x':10,'y':20,'zoom':1})

    def test_export_presentation_cannot_override_owner_ports_or_runtime(self):
        original=json.loads((Path(__file__).parent/'fixtures/flow-ts081.json').read_text(encoding='utf-8'))
        mutations=[lambda g:g['metadata'].update(owner_id=str(self.other.pk)),
                   lambda g:g['graph']['nodes'][2].update(outputs=[{'id':'out'}]),
                   lambda g:g['view'].update(url='https://invalid.example'),
                   lambda g:g['graph']['nodes'][1].update(startingMsg={'command':'invalid'})]
        for change in mutations:
            schema=deepcopy(original);change(schema)
            with self.assertRaises(flows.InvalidWorkflow): self.save(schema)
        self.assertEqual(Definition.objects.count(),0)

    def test_fresh_coordinator_uses_checkpoint_without_reexecuting_completed_nodes(self):
        run = self.launch(); first = flows.advance(self.owner, run['id'], 0)
        checkpoint = NodeCheckpoint.objects.get(node_id='start'); first_attempt = checkpoint.first_attempt_at
        second = flows.advance(self.owner, run['id'], 1)
        third = flows.advance(self.owner, run['id'], 2)
        checkpoint.refresh_from_db()
        self.assertEqual((first['status'],second['status'],third['status']), ('running','running','completed'))
        self.assertEqual((checkpoint.attempts,checkpoint.first_attempt_at), (1,first_attempt))
        self.assertEqual(flows.advance(self.owner, run['id'], 3), third)
        third['outputs']['start']['name'] = 'Caller mutation'
        self.assertEqual(flows.get_run(self.owner,run['id'])['outputs']['start']['name'], 'Ada')

    def test_duplicate_delivery_reuses_exact_run_but_conflicting_payload_refuses(self):
        saved = self.save(); first = self.launch(saved)
        self.assertEqual(self.launch(saved), first)
        with self.assertRaises(flows.Conflict): self.launch(saved, {'name':'Different'})
        newer = self.save(graph('New'), definition_id=saved['id'], expected_revision=1)
        with self.assertRaises(flows.Conflict): self.launch(newer)
        self.assertEqual(WorkflowRun.objects.count(), 1)

    def test_graph_edit_cannot_change_running_snapshot_or_seed(self):
        saved = self.save(); run = self.launch(saved)
        flows.advance(self.owner,run['id'],0)
        self.save(graph('Other revision'), definition_id=saved['id'], expected_revision=1)
        for step in (1,2): run = flows.advance(self.owner,run['id'],step)
        self.assertEqual(run['definition_revision'], 1)
        self.assertEqual(run['outputs']['result'], 'Ready Ada')
        model = WorkflowRun.objects.get(pk=run['id']); model.seed = {'name':'rewritten'}
        with self.assertRaises(ValueError): model.save()
        self.assertEqual(WorkflowRun.objects.get(pk=run['id']).seed, {'name':'Ada'})

    def test_cancel_is_revision_checked_terminal_and_preserves_accepted_checkpoint(self):
        run = self.launch(); run = flows.advance(self.owner,run['id'],0)
        with self.assertRaises(flows.Conflict): flows.cancel(self.owner,run['id'],0)
        stopped = flows.cancel(self.owner,run['id'],1)
        self.assertEqual((stopped['status'],stopped['step'],set(stopped['outputs'])), ('cancelled',2,{'start'}))
        self.assertEqual(flows.cancel(self.owner,run['id'],2), stopped)
        self.assertEqual(flows.advance(self.owner,run['id'],2), stopped)
        self.assertEqual(NodeCheckpoint.objects.count(), 1)

    def test_another_actor_cannot_inspect_or_mutate_definition_run_or_checkpoint(self):
        saved = self.save(); run = self.launch(saved)
        operations = [lambda: flows.get_definition(self.other,saved['id']),lambda: flows.get_run(self.other,run['id']),
                      lambda: flows.dispatch(self.other,saved['id'],1,{},'foreign'),
                      lambda: flows.advance(self.other,run['id'],0),lambda: flows.cancel(self.other,run['id'],0),
                      lambda: flows.save_definition(self.other,'Intrusion',graph(),definition_id=saved['id'],expected_revision=1)]
        for operation in operations:
            with self.assertRaises(flows.Unavailable) as caught: operation()
            self.assertEqual(str(caught.exception),'Workflow unavailable')
        self.assertEqual((DefinitionVersion.objects.count(),WorkflowRun.objects.count(),NodeCheckpoint.objects.count()),(1,1,0))

    def test_stale_user_object_cannot_bypass_current_enabled_role_or_active_authority(self):
        run = self.launch(); run = flows.advance(self.owner,run['id'],0)
        for field,value in (('enabled',False),('role','viewer')):
            Membership.objects.filter(pk=self.member.pk).update(**{field:value})
            with self.assertRaises(flows.Unavailable): flows.advance(self.owner,run['id'],1)
            with self.assertRaises(flows.Unavailable): flows.get_run(self.owner,run['id'])
            Membership.objects.filter(pk=self.member.pk).update(enabled=True,role='preparer')
        get_user_model().objects.filter(pk=self.owner.pk).update(is_active=False)
        self.assertTrue(self.owner.is_active)
        with self.assertRaises(flows.Unavailable): flows.advance(self.owner,run['id'],1)
        self.assertEqual(NodeCheckpoint.objects.count(),1)

    def test_changed_entity_ownership_stops_next_node_and_cross_scope_save(self):
        run = self.launch(); flows.advance(self.owner,run['id'],0)
        Engagement.objects.filter(pk=self.engagement.pk).update(owner=self.other)
        with self.assertRaises(flows.Unavailable): flows.advance(self.owner,run['id'],1)
        with self.assertRaises(flows.Unavailable): self.save()
        self.assertEqual(NodeCheckpoint.objects.count(),1)

    def test_unbounded_nonjson_private_scope_overrides_and_invalid_dispatch_refuse(self):
        saved = self.save()
        for seed in ([], {'bad':float('nan')}, {'bad':object()}, {'bad':'x'*32769}, {'bad':'x\x00'}):
            with self.subTest(seed_type=type(seed)), self.assertRaises(flows.InvalidWorkflow): self.launch(saved,seed)
        for key in ('', 'x'*129, 'bad\nkey'):
            with self.assertRaises(flows.InvalidWorkflow): self.launch(saved,key=key)
        with self.assertRaises(flows.Conflict): flows.dispatch(self.owner,saved['id'],True,{},'valid')
        hidden = graph(); hidden['owner_id'] = str(self.other.pk)
        with self.assertRaises(flows.InvalidWorkflow): self.save(hidden)
        self.assertEqual(WorkflowRun.objects.count(),0)

    def test_transaction_crash_rolls_back_claim_and_output_before_safe_retry(self):
        run = self.launch(); complete = DjangoClaimStore.complete
        def crash(store,*args):
            complete(store,*args)
            raise RuntimeError('Synthetic interrupted transaction')
        with patch.object(DjangoClaimStore,'complete',crash), self.assertRaises(RuntimeError):
            flows.advance(self.owner,run['id'],0)
        self.assertEqual(NodeCheckpoint.objects.count(),0)
        self.assertEqual(flows.get_run(self.owner,run['id'])['step'],0)
        self.assertEqual(self.finish(run)['outputs']['result'],'Ready Ada')

    def test_oversized_expansion_fails_with_redacted_diagnostic_and_no_partial_node(self):
        run = self.launch(self.save(graph('{{ in.name }}'*120)), {'name':'Synthetic private data '*40})
        run = flows.advance(self.owner,run['id'],0)
        failed = flows.advance(self.owner,run['id'],1)
        self.assertEqual((failed['status'],failed['error']),('failed','output_limit'))
        self.assertNotIn('Synthetic private data',json.dumps(failed['error']))
        self.assertEqual(set(failed['outputs']),{'start'})
        self.assertEqual(failed['nodes']['format']['status'],'failed')
        self.assertEqual(failed['nodes']['format']['error'],'output_limit')
        self.assertEqual(flows.advance(self.owner,run['id'],2),failed)

    def test_store_claim_reentry_is_owner_fenced_and_first_attempt_clock_stays_fixed(self):
        run = self.launch(); model = WorkflowRun.objects.get(pk=run['id'])
        with transaction.atomic():
            store = DjangoClaimStore(model)
            self.assertTrue(store.claim(run['id'],'start','one'))
            initial = store.state(run['id'])['start'].first_attempt_at
            self.assertFalse(store.claim(run['id'],'start','two'))
            self.assertTrue(store.claim(run['id'],'start','one'))
            row = store.state(run['id'])['start']
            self.assertEqual((row.attempts,row.first_attempt_at),(2,initial))
            store.complete(run['id'],'start',{'literal':None},('out',))
            self.assertFalse(store.claim(run['id'],'start','one'))
            # This is Fancy's NodeClaimStore.skip operation, not a skipped test.
            settle_unreachable = getattr(store, 'skip')
            self.assertFalse(settle_unreachable(run['id'],'start'))
            self.assertEqual(store.state(run['id'])['start'].output,{'literal':None})
            with self.assertRaises(flows.Unavailable): store.state(str(uuid.uuid4()))
            with self.assertRaises(flows.Unavailable): store.complete(run['id'],'format','unclaimed',('out',))
            with self.assertRaises(flows.Unavailable): store.claim(run['id'],'unknown-node','one')
            self.assertEqual(set(store.state(run['id'])),{'start'})


class WorkflowConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.install, self.owner, self.other, self.member, self.engagement, self.entity = fixture()
        self.saved = flows.save_definition(self.owner,'Concurrent synthetic',graph(),entity_id=str(self.entity.pk))

    def race(self, operation):
        barrier = Barrier(2)
        def call():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try: return operation()
                except flows.Conflict: return 'conflict'
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool: return list(pool.map(lambda _:call(),range(2)))

    def test_concurrent_duplicate_delivery_creates_one_persistent_run(self):
        results = self.race(lambda:flows.dispatch(self.owner,self.saved['id'],1,{'name':'Ada'},'one-key'))
        self.assertEqual([r['id'] for r in results],[results[0]['id']]*2)
        self.assertEqual(WorkflowRun.objects.count(),1)

    def test_two_workers_with_same_dispatch_step_execute_only_one_node(self):
        run = flows.dispatch(self.owner,self.saved['id'],1,{'name':'Ada'},'step-key')
        results = self.race(lambda:flows.advance(self.owner,run['id'],0))
        self.assertEqual(results.count('conflict'),1)
        self.assertEqual(NodeCheckpoint.objects.count(),1)
        self.assertEqual(NodeCheckpoint.objects.get().attempts,1)
        self.assertEqual(flows.get_run(self.owner,run['id'])['step'],1)
