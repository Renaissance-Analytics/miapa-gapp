"""Django adapter for Fancy's public NodeClaimStore protocol.

The host holds one database transaction and the installation access write
boundary for each deterministic step. A killed transaction rolls back both the
claim and its output. There are no external effects to repeat. Do not extend
this recovery contract to financial/network/process executors.
"""
from django.db import connection
from django.db.models import F
from fancy_flow.durable import NodeRunStatus, NodeState

from .errors import InvalidWorkflow, OutputLimit, Unavailable
from .models import NodeCheckpoint
from .schema import bounded_json


class DjangoClaimStore:
    def __init__(self, run):
        self.run = run
        self.owners = {}
        self.node_ids = {node['id'] for node in run.version.schema['graph']['nodes']}

    def _bound(self, run_key, mutation=False):
        if (str(self.run.pk) != run_key or not connection.in_atomic_block
                or (mutation and self.run.status not in ('queued','running'))):
            raise Unavailable()

    def claim(self, run_key, node_id, owner):
        self._bound(run_key,True)
        if node_id not in self.node_ids or type(owner) is not str or not 0 < len(owner) <= 64:
            raise Unavailable()
        row,created = NodeCheckpoint.objects.get_or_create(run=self.run,node_id=node_id,
            defaults={'status':'claimed','owner':owner,'attempts':1})
        if not created:
            changed = NodeCheckpoint.objects.filter(pk=row.pk,owner=owner,status__in=NodeRunStatus.HELD).update(
                status='claimed',attempts=F('attempts')+1)
            if changed != 1:
                return False
        self.owners[node_id] = owner
        return True

    def state(self, run_key):
        self._bound(run_key)
        return {row.node_id:NodeState(status=row.status,ports=tuple(row.ports),output=row.output,error=row.error,
            owner=row.owner,attempts=row.attempts,first_attempt_at=row.first_attempt_at.isoformat())
            for row in self.run.checkpoints.order_by('pk')}

    def _write(self, run_key, node_id, **values):
        self._bound(run_key,True)
        owner = self.owners.get(node_id)
        if node_id not in self.node_ids or owner is None or NodeCheckpoint.objects.filter(run=self.run,node_id=node_id,owner=owner,status='claimed').update(**values) != 1:
            raise Unavailable()

    def complete(self, run_key, node_id, output, ports):
        self._bound(run_key,True)
        try:
            output,_ = bounded_json(output)
        except InvalidWorkflow:
            raise OutputLimit() from None
        if type(ports) is not tuple or any(port != 'out' for port in ports):
            raise Unavailable()
        self._write(run_key,node_id,status='completed',output=output,ports=list(ports),error=None)

    def skip(self, run_key, node_id):
        self._bound(run_key,True)
        if node_id not in self.node_ids:
            raise Unavailable()
        row,created = NodeCheckpoint.objects.get_or_create(run=self.run,node_id=node_id,defaults={'status':'skipped'})
        if created:
            return True
        if row.status in NodeRunStatus.SETTLED:
            return False
        self._write(run_key,node_id,status='skipped',ports=[],error=None)
        return True

    def fail(self, run_key, node_id, error):
        # Package error strings may contain submitted values. Persist a closed code.
        self._write(run_key,node_id,status='failed',ports=[],error='output_limit' if error=='output_limit' else 'execution_failed')

    def pause(self, run_key, node_id, reason):
        # Human gates need a separate recorded-response authority contract.
        raise Unavailable()
