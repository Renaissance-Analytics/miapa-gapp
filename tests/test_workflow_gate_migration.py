"""A downgrade cannot strip accepted human response history."""
import uuid
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from backend.maipa.workflows import services as flows,gates
from backend.maipa.workflows.worker import process_job
from backend.maipa.workflows.models import HumanResponse
from tests.test_workflows import fixture
from tests.test_workflow_gates import gated_graph


class HumanGateMigrationTests(TransactionTestCase):
    def test_reverse_migration_refuses_before_removing_recorded_human_evidence(self):
        install,owner,other,member,engagement,entity=fixture()
        saved=flows.save_definition(owner,'Synthetic migration gate',gated_graph())
        run=flows.dispatch(owner,saved['id'],1,{'name':'Ada'},'migration-human')
        process_job(run['id'],0);process_job(run['id'],1);gate=gates.get_pending(owner,run['id'])
        receipt=gates.respond(owner,run['id'],gate['id'],expected_step=2,definition_revision=1,
            request_sha256=gate['request_sha256'],request_key=str(uuid.uuid4()),answer=False)
        process_job(run['id'],3);process_job(run['id'],4)
        self.assertEqual(flows.get_run(owner,run['id'])['status'],'completed')
        try:
            with self.assertRaisesRegex(RuntimeError,'offline backup recovery'):
                MigrationExecutor(connection).migrate([('workflows','0003_native_authority')])
            self.assertEqual(HumanResponse.objects.filter(pk=receipt['id']).count(),1)
        finally:
            executor=MigrationExecutor(connection);executor.migrate(executor.loader.graph.leaf_nodes())
