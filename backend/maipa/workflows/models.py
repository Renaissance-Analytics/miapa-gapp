"""Host persistence, never a second graph engine or a business effect ledger."""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

RUN_STATUSES = ('queued', 'running', 'completed', 'failed', 'cancelled')
NODE_STATUSES = ('claimed', 'completed', 'skipped', 'failed', 'paused')


class Definition(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, editable=False)
    installation = models.ForeignKey('workspace.Installation', on_delete=models.PROTECT, editable=False)
    entity = models.ForeignKey('intake.Entity', null=True, on_delete=models.PROTECT, editable=False)
    name = models.CharField(max_length=128)
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(revision__gt=0, revision__lt=2_000_000_000), name='flow_definition_revision')]


class DefinitionVersion(models.Model):
    definition = models.ForeignKey(Definition, on_delete=models.PROTECT, related_name='versions', editable=False)
    revision = models.PositiveIntegerField(editable=False)
    schema = models.JSONField(editable=False)
    schema_sha256 = models.CharField(max_length=64, editable=False)
    runtime = models.CharField(max_length=32, editable=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('definition','revision'), name='flow_version_identity'),
                       models.CheckConstraint(condition=models.Q(revision__gt=0, revision__lt=2_000_000_000), name='flow_version_revision')]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError('Workflow versions are append-only')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('Workflow versions are append-only')


class WorkflowRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    definition = models.ForeignKey(Definition, on_delete=models.PROTECT, editable=False)
    version = models.ForeignKey(DefinitionVersion, on_delete=models.PROTECT, editable=False)
    dispatch_key = models.CharField(max_length=128, editable=False)
    seed = models.JSONField(editable=False)
    seed_sha256 = models.CharField(max_length=64, editable=False)
    access_revision = models.PositiveIntegerField(editable=False)
    status = models.CharField(max_length=12, default='queued')
    step = models.PositiveIntegerField(default=0)
    error = models.CharField(max_length=32, null=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('definition','dispatch_key'), name='flow_dispatch_identity'),
                       models.CheckConstraint(condition=models.Q(status__in=RUN_STATUSES), name='flow_run_status'),
                       models.CheckConstraint(condition=models.Q(step__lt=2_000_000_000), name='flow_run_step')]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            fields = ('definition_id','version_id','dispatch_key','seed','seed_sha256','access_revision','created_at')
            original = type(self).objects.filter(pk=self.pk).values(*fields).first()
            if original is None or any(original[field] != getattr(self,field) for field in fields):
                raise ValueError('Workflow run inputs are immutable')
        return super().save(*args, **kwargs)


class NodeCheckpoint(models.Model):
    run = models.ForeignKey(WorkflowRun, on_delete=models.PROTECT, related_name='checkpoints')
    node_id = models.CharField(max_length=80)
    status = models.CharField(max_length=12)
    owner = models.CharField(max_length=64, null=True)
    output = models.JSONField(null=True)
    ports = models.JSONField(default=list)
    error = models.CharField(max_length=32, null=True)
    attempts = models.PositiveIntegerField(default=0)
    first_attempt_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('run','node_id'), name='flow_node_claim_identity'),
                       models.CheckConstraint(condition=models.Q(status__in=NODE_STATUSES), name='flow_node_status')]
