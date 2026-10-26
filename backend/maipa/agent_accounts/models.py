"""Server-owned account metadata, never provider credentials or login state."""
import uuid

from django.conf import settings
from django.db import models


class AgentAccount(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    provider = models.CharField(max_length=16, choices=[("codex", "Codex"), ("claude", "Claude")])
    label = models.CharField(max_length=80)
    enabled = models.BooleanField(default=True)
    last_check_started_at = models.DateTimeField(null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["owner", "provider"], name="agent_account_owner_provider_unique"),
            models.CheckConstraint(condition=models.Q(provider__in=["codex", "claude"]), name="agent_account_known_provider"),
        ]


class TransportCheck(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(AgentAccount, on_delete=models.CASCADE, related_name="transport_checks")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, editable=False)
    status = models.CharField(max_length=24, default="running")
    provider = models.CharField(max_length=16)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(status__in=["running", "transport_verified", "unavailable"]), name="agent_check_known_status"),
            models.CheckConstraint(condition=models.Q(provider__in=["codex", "claude"]), name="agent_check_known_provider"),
        ]


class ProviderProfile(models.Model):
    """Original private execution binding; never a login/readiness declaration."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.OneToOneField(AgentAccount, on_delete=models.PROTECT, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, editable=False)
    installation = models.ForeignKey('workspace.Installation', on_delete=models.PROTECT, editable=False)
    storage_id = models.UUIDField(editable=False)
    provider = models.CharField(max_length=16, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(provider__in=['codex', 'claude']),
                                   name='agent_profile_known_provider'),
        ]


class SessionReference(models.Model):
    """Trusted host's durable private protocol mapping, never a readiness flag."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, editable=False)
    account = models.ForeignKey(AgentAccount, on_delete=models.PROTECT, editable=False)
    installation = models.ForeignKey('workspace.Installation', on_delete=models.PROTECT, editable=False)
    storage_id = models.UUIDField(editable=False)
    provider = models.CharField(max_length=16, editable=False)
    private_handle = models.CharField(max_length=4096, editable=False)
    handle_digest = models.CharField(max_length=64, editable=False)
    revoked_at = models.DateTimeField(null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['account', 'provider', 'handle_digest'], name='agent_session_handle_unique'),
            models.CheckConstraint(condition=models.Q(provider__in=['codex', 'claude']), name='agent_session_known_provider'),
        ]


class ArtifactReference(models.Model):
    """Immutable payload receipt; private location derives only from the server UUID."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(SessionReference, on_delete=models.PROTECT, editable=False)
    size = models.PositiveIntegerField(editable=False)
    sha256 = models.CharField(max_length=64, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(size__lte=1_048_576), name='agent_artifact_size_budget'),
        ]
