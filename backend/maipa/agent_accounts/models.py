"""Server-owned account metadata, never provider credentials or login state."""
import uuid

from django.conf import settings
from django.db import models
from django.core.validators import RegexValidator
from django.db.models.functions import Length, Substr
from django.db.models.lookups import Exact, In, LessThanOrEqual
from .providers import PROVIDER_ID


def provider_id_condition():
    # Native SQL builtins keep offline SQLite integrity/backup usable without
    # Django's connection-local REGEXP function. LTRIM removes the whole value
    # only when every character belongs to the explicit registration alphabet.
    alphabet = 'abcdefghijklmnopqrstuvwxyz'
    return (Exact(models.Func(models.F('provider'), models.Value(alphabet + '0123456789-'), function='LTRIM'), models.Value(''))
            & In(Substr('provider', 1, 1), list(alphabet))
            & LessThanOrEqual(Length('provider'), models.Value(64)))


class AgentAccount(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    provider = models.CharField(max_length=64, validators=[RegexValidator(PROVIDER_ID)])
    label = models.CharField(max_length=80)
    enabled = models.BooleanField(default=True)
    last_check_started_at = models.DateTimeField(null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["owner", "provider"], name="agent_account_owner_provider_unique"),
            models.CheckConstraint(condition=provider_id_condition(), name="agent_account_provider_id"),
        ]


class TransportCheck(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(AgentAccount, on_delete=models.CASCADE, related_name="transport_checks")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, editable=False)
    status = models.CharField(max_length=24, default="running")
    provider = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(status__in=["running", "transport_verified", "unavailable"]), name="agent_check_known_status"),
            models.CheckConstraint(condition=provider_id_condition(), name="agent_check_provider_id"),
        ]


class ProviderProfile(models.Model):
    """Original private execution binding; never a login/readiness declaration."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.OneToOneField(AgentAccount, on_delete=models.PROTECT, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, editable=False)
    installation = models.ForeignKey('workspace.Installation', on_delete=models.PROTECT, editable=False)
    storage_id = models.UUIDField(editable=False)
    provider = models.CharField(max_length=64, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=provider_id_condition(),
                                   name='agent_profile_provider_id'),
        ]


class SessionReference(models.Model):
    """Trusted host's durable private protocol mapping, never a readiness flag."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, editable=False)
    account = models.ForeignKey(AgentAccount, on_delete=models.PROTECT, editable=False)
    installation = models.ForeignKey('workspace.Installation', on_delete=models.PROTECT, editable=False)
    storage_id = models.UUIDField(editable=False)
    provider = models.CharField(max_length=64, editable=False)
    private_handle = models.CharField(max_length=4096, editable=False)
    handle_digest = models.CharField(max_length=64, editable=False)
    revoked_at = models.DateTimeField(null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['account', 'provider', 'handle_digest'], name='agent_session_handle_unique'),
            models.CheckConstraint(condition=provider_id_condition(), name='agent_session_provider_id'),
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


class UiComposition(models.Model):
    """One contextual composition bound to an original private ACP session."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(SessionReference, on_delete=models.PROTECT, editable=False)
    role = models.CharField(max_length=16, editable=False)
    access_revision = models.PositiveIntegerField(editable=False)
    revision = models.PositiveIntegerField(default=0)
    composition = models.JSONField(null=True, default=None)
    form_drafts = models.JSONField(default=dict, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(role__in=('administrator', 'preparer')), name='agent_ui_known_role'),
            models.CheckConstraint(condition=models.Q(revision__lt=2_000_000_000), name='agent_ui_revision_budget'),
        ]


class ChatSelection(models.Model):
    """Saved owned connection choice; revision prevents stale cross-account writes."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, primary_key=True, on_delete=models.CASCADE)
    account = models.ForeignKey(AgentAccount, null=True, on_delete=models.PROTECT)
    revision = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(revision__lt=2_000_000_000),
                                              name='agent_selection_revision_budget')]


class Conversation(models.Model):
    """A user's durable transcript. Worker identity and UI authority stay server-owned."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.OneToOneField(AgentAccount, on_delete=models.PROTECT)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, editable=False)
    installation = models.ForeignKey('workspace.Installation', on_delete=models.PROTECT, editable=False)
    storage_id = models.UUIDField(editable=False)
    revision = models.PositiveIntegerField(default=0)
    messages = models.JSONField(default=list)
    failure = models.JSONField(null=True, default=None, editable=False)
    requests = models.JSONField(default=list)
    phase = models.CharField(max_length=16, default='offline')
    worker = models.UUIDField(null=True, default=None, editable=False)
    heartbeat = models.DateTimeField(null=True, editable=False)
    ui = models.ForeignKey(UiComposition, null=True, on_delete=models.PROTECT)
