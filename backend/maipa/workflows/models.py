"""Host persistence, never a second graph engine or a business effect ledger."""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

RUN_STATUSES = ('queued', 'running', 'waiting', 'blocked', 'completed', 'failed', 'cancelled')
NODE_STATUSES = ('claimed', 'completed', 'skipped', 'failed', 'paused', 'released')


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


class WorkflowDraft(models.Model):
    """Mutable authoring, never a dispatchable version or authority grant."""
    id = models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,editable=False)
    installation = models.ForeignKey('workspace.Installation',on_delete=models.PROTECT,editable=False)
    entity = models.ForeignKey('intake.Entity',null=True,on_delete=models.PROTECT,editable=False)
    name = models.CharField(max_length=128)
    create_key = models.UUIDField(editable=False)
    create_sha256 = models.CharField(max_length=64,editable=False)
    revision = models.PositiveIntegerField(default=0)
    graph = models.JSONField()
    graph_sha256 = models.CharField(max_length=64)
    definition = models.OneToOneField(Definition,null=True,on_delete=models.PROTECT,editable=False)
    published_draft_revision = models.PositiveIntegerField(null=True,editable=False)
    published_definition_revision = models.PositiveIntegerField(null=True,editable=False)
    created_at = models.DateTimeField(default=timezone.now,editable=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('owner','installation','create_key'),name='flow_draft_create_identity'),
            models.CheckConstraint(condition=models.Q(revision__lt=2_000_000_000),name='flow_draft_revision')]

    def save(self,*args,**kwargs):
        if not self._state.adding:
            fields=('owner_id','installation_id','entity_id','name','create_key','create_sha256','created_at')
            original=type(self).objects.filter(pk=self.pk).values(*fields).first()
            if original is None or any(original[key]!=getattr(self,key) for key in fields):
                raise ValueError('Original workflow draft scope is immutable')
        return super().save(*args,**kwargs)


class WorkflowRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    definition = models.ForeignKey(Definition, on_delete=models.PROTECT, editable=False)
    version = models.ForeignKey(DefinitionVersion, on_delete=models.PROTECT, editable=False)
    dispatch_key = models.CharField(max_length=128, editable=False)
    seed = models.JSONField(editable=False)
    seed_sha256 = models.CharField(max_length=64, editable=False)
    access_revision = models.PositiveIntegerField(editable=False)
    # Null only for records predating verified origin capture. Never infer an
    # old run's actor from a possibly transferred definition during migration.
    origin_owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, editable=False)
    origin_installation = models.ForeignKey('workspace.Installation', null=True, on_delete=models.PROTECT, editable=False)
    origin_entity = models.ForeignKey('intake.Entity', null=True, on_delete=models.PROTECT, editable=False)
    origin_kind = models.CharField(max_length=8, default='local', editable=False)
    status = models.CharField(max_length=12, default='queued')
    step = models.PositiveIntegerField(default=0)
    error = models.CharField(max_length=32, null=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        indexes = [models.Index(fields=('status','created_at','id'), name='flow_dispatch_queue')]
        constraints = [models.UniqueConstraint(fields=('definition','dispatch_key'), name='flow_dispatch_identity'),
                       models.CheckConstraint(condition=models.Q(status__in=RUN_STATUSES), name='flow_run_status'),
                       models.CheckConstraint(condition=models.Q(origin_kind__in=('local','native')), name='flow_run_origin_kind'),
                       models.CheckConstraint(condition=models.Q(step__lt=2_000_000_000), name='flow_run_step')]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            fields = ('definition_id','version_id','dispatch_key','seed','seed_sha256','access_revision','created_at',
                      'origin_owner_id','origin_installation_id','origin_entity_id','origin_kind')
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


class WorkflowEvidence(models.Model):
    """Trusted provenance and accepted responses, never rewritten in place."""
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError('Workflow evidence is append-only')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('Workflow evidence is append-only')


class NativePrincipalBinding(WorkflowEvidence):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    installation = models.ForeignKey('workspace.Installation', on_delete=models.PROTECT)
    account = models.ForeignKey('agent_accounts.AgentAccount', on_delete=models.PROTECT)
    session = models.ForeignKey('agent_accounts.SessionReference', on_delete=models.PROTECT)
    ui = models.ForeignKey('agent_accounts.UiComposition', on_delete=models.PROTECT)
    agent = models.OneToOneField('policies.AgentPrincipal', on_delete=models.PROTECT)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(default=timezone.now, editable=False)


class NativeGraphBinding(WorkflowEvidence):
    version = models.OneToOneField(DefinitionVersion, on_delete=models.PROTECT)
    resource = models.OneToOneField('policies.PolicyResource', on_delete=models.PROTECT)
    resource_revision = models.PositiveIntegerField()
    created_at = models.DateTimeField(default=timezone.now, editable=False)


class NativeRunAuthority(WorkflowEvidence):
    run = models.OneToOneField(WorkflowRun, on_delete=models.PROTECT)
    binding = models.ForeignKey(NativePrincipalBinding, on_delete=models.PROTECT)
    graph = models.ForeignKey(NativeGraphBinding, on_delete=models.PROTECT)
    grant = models.ForeignKey('policies.AuthorityGrant', on_delete=models.PROTECT)
    created_at = models.DateTimeField(default=timezone.now, editable=False)


class HumanGate(WorkflowEvidence):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(WorkflowRun, on_delete=models.PROTECT, related_name='human_gates')
    node_id = models.CharField(max_length=80)
    kind = models.CharField(max_length=16)
    request = models.JSONField()
    observed_input = models.JSONField()
    input_sha256 = models.CharField(max_length=64)
    request_sha256 = models.CharField(max_length=64)
    parked_step = models.PositiveIntegerField()
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('run','node_id'),name='flow_human_gate_identity'),
            models.CheckConstraint(condition=models.Q(kind__in=('human_approval','user_input')),name='flow_human_gate_kind')]


class HumanResponse(WorkflowEvidence):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    gate = models.OneToOneField(HumanGate, on_delete=models.PROTECT, related_name='response')
    human = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    request_key = models.UUIDField()
    answer = models.JSONField()
    answer_sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
