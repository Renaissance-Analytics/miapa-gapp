"""Durable policy evidence. Application mutations append records, never rewrite history."""
import uuid

from django.conf import settings
from django.db import models


class Immutable(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError('Policy evidence is append-only')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('Policy evidence is append-only')


class AgentPrincipal(models.Model):
    """Trusted server registration, separate from a provider account or browser session."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    installation = models.ForeignKey('workspace.Installation', on_delete=models.PROTECT)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    reference = models.CharField(max_length=256)
    enabled = models.BooleanField(default=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)


class PolicyEpoch(models.Model):
    entity = models.OneToOneField('intake.Entity', primary_key=True, on_delete=models.PROTECT)
    version = models.PositiveIntegerField(default=0)


class AuthorityGrant(Immutable):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.ForeignKey('intake.Entity', on_delete=models.PROTECT)
    subject = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    agent = models.ForeignKey(AgentPrincipal, null=True, on_delete=models.PROTECT)
    action = models.CharField(max_length=64)
    duty = models.CharField(max_length=16)
    limit = models.CharField(max_length=24, null=True)
    currency = models.CharField(max_length=3, null=True)
    starts_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    evidence = models.CharField(max_length=256)
    parent = models.ForeignKey('self', null=True, on_delete=models.PROTECT)
    delegated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name='+')
    can_delegate = models.BooleanField(default=False)
    revoked_at = models.DateTimeField(null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(expires_at__gt=models.F('starts_at')), name='policy_grant_interval'),
            models.CheckConstraint(condition=models.Q(duty__in=['preparer', 'reviewer', 'approver', 'executor']), name='policy_grant_duty'),
            models.CheckConstraint(condition=(models.Q(limit__isnull=True, currency__isnull=True)
                | models.Q(limit__isnull=False, currency__isnull=False)), name='policy_grant_money_pair'),
            models.CheckConstraint(condition=(models.Q(agent__isnull=True)
                | models.Q(duty__in=['preparer', 'executor'], can_delegate=False)), name='policy_agent_duty'),
        ]
        indexes = [models.Index(fields=['entity', 'subject', 'action', 'duty'], name='policy_grant_lookup')]


class GrantRevocation(Immutable):
    grant = models.OneToOneField(AuthorityGrant, on_delete=models.PROTECT)
    actor = models.CharField(max_length=64)
    reason = models.CharField(max_length=256)
    created_at = models.DateTimeField(auto_now_add=True)


class PolicyResource(models.Model):
    """Scope/version contract for future actual graph/run/job/log/checkpoint stores."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.ForeignKey('intake.Entity', on_delete=models.PROTECT)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    kind = models.CharField(max_length=16)
    revision = models.PositiveIntegerField(default=1)
    graph_revision = models.CharField(max_length=128)
    parent = models.ForeignKey('self', null=True, on_delete=models.PROTECT)
    enabled = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(kind__in=['graph', 'run', 'job', 'log', 'checkpoint']), name='policy_resource_kind'),
            models.CheckConstraint(condition=models.Q(revision__gte=1), name='policy_resource_revision'),
        ]


class Authorization(Immutable):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.ForeignKey('intake.Entity', on_delete=models.PROTECT)
    preparer = models.CharField(max_length=64)
    preparer_grant = models.ForeignKey(AuthorityGrant, on_delete=models.PROTECT)
    policy_version = models.PositiveIntegerField()
    resource_revision = models.PositiveIntegerField(null=True)
    snapshot = models.JSONField()
    snapshot_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)


class Attestation(Immutable):
    authorization = models.ForeignKey(Authorization, on_delete=models.PROTECT)
    actor = models.CharField(max_length=64)
    duty = models.CharField(max_length=16)
    grant = models.ForeignKey(AuthorityGrant, on_delete=models.PROTECT)
    snapshot_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['authorization', 'duty'], name='policy_one_attestation_per_duty'),
            models.CheckConstraint(condition=models.Q(duty__in=['reviewer', 'approver']), name='policy_attestation_duty')]


class PolicyAudit(Immutable):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.ForeignKey('intake.Entity', null=True, on_delete=models.PROTECT)
    authorization = models.ForeignKey(Authorization, null=True, on_delete=models.PROTECT)
    actor = models.CharField(max_length=64)
    operation = models.CharField(max_length=64)
    allowed = models.BooleanField()
    code = models.CharField(max_length=32)
    snapshot_hash = models.CharField(max_length=64, default='')
    policy_version = models.PositiveIntegerField(default=0)
    grant_ids = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
