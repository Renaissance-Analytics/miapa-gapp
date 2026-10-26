"""One server-owned installation and explicit user membership; no SaaS tenant switch."""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

ROLES = ("administrator", "preparer", "reviewer", "viewer")


class Installation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slot = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)
    access_revision = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(slot=1), name="workspace_single_installation"),
            models.CheckConstraint(condition=models.Q(access_revision__lt=2_000_000_000), name='workspace_access_revision'),
        ]


class Membership(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    installation = models.ForeignKey(Installation, on_delete=models.PROTECT)
    role = models.CharField(max_length=16, choices=[(role, role.title()) for role in ROLES])
    enabled = models.BooleanField(default=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(role__in=ROLES), name="workspace_known_role")]


class AccessEvent(models.Model):
    """Append-only through the access service; no password or provider state."""
    installation = models.ForeignKey(Installation, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    subject = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    kind = models.CharField(max_length=8)
    role = models.CharField(max_length=16)
    enabled = models.BooleanField()
    revision = models.PositiveIntegerField()
    at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=('installation', 'revision'), name='workspace_access_event_revision'),
            models.CheckConstraint(condition=models.Q(kind__in=('created', 'changed')), name='workspace_access_event_kind'),
            models.CheckConstraint(condition=models.Q(role__in=ROLES), name='workspace_access_event_role'),
            models.CheckConstraint(condition=models.Q(revision__gt=0, revision__lt=2_000_000_000), name='workspace_access_event_bounds'),
        ]


class LoginBudget(models.Model):
    fingerprint = models.CharField(max_length=64, primary_key=True)
    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(attempts__lte=5), name="workspace_login_attempt_budget")]


class BootstrapState(models.Model):
    """Migration-created write lock; closure survives users being disabled/deleted."""
    slot = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    closed = models.BooleanField(default=False)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(slot=1), name='workspace_single_bootstrap')]


class AccountOnboarding(models.Model):
    identity = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    installation = models.ForeignKey(Installation, on_delete=models.PROTECT)
    revision = models.PositiveIntegerField(default=0)
    step = models.CharField(max_length=24, default='display_name')
    draft = models.JSONField(default=dict)
    messages = models.JSONField(default=list)
    entity = models.ForeignKey('intake.Entity', null=True, on_delete=models.PROTECT)
    deferred = models.BooleanField(default=False)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(revision__lt=2_000_000_000), name='workspace_setup_revision')]


class WorkspaceLayout(models.Model):
    """Personal presentation only; its identity binds writes across cookie changes."""
    identity = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    installation = models.ForeignKey(Installation, on_delete=models.PROTECT)
    revision = models.PositiveIntegerField(default=0)
    split_basis_points = models.PositiveSmallIntegerField(default=2800)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(revision__lt=2_000_000_000), name='workspace_layout_revision'),
            models.CheckConstraint(condition=models.Q(split_basis_points__gte=2200, split_basis_points__lte=4500), name='workspace_layout_bounds'),
        ]


class WorkspacePreferences(models.Model):
    """Personal choices only; independent identity/revision from pane gestures."""
    identity = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    installation = models.ForeignKey(Installation, on_delete=models.PROTECT)
    revision = models.PositiveIntegerField(default=0)
    theme = models.CharField(max_length=8, default='system')
    active_view = models.CharField(max_length=24, default='client-facts')

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(revision__lt=2_000_000_000), name='workspace_preferences_revision'),
            models.CheckConstraint(condition=models.Q(theme__in=('system', 'light', 'dark')), name='workspace_preferences_theme'),
            models.CheckConstraint(condition=models.Q(active_view__in=('client-facts', 'workspace-guide')), name='workspace_preferences_view'),
        ]
