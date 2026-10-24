"""One server-owned installation and explicit user membership; no SaaS tenant switch."""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

ROLES = ("administrator", "preparer", "reviewer", "viewer")


class Installation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slot = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(slot=1), name="workspace_single_installation")]


class Membership(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    installation = models.ForeignKey(Installation, on_delete=models.PROTECT)
    role = models.CharField(max_length=16, choices=[(role, role.title()) for role in ROLES])
    enabled = models.BooleanField(default=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(role__in=ROLES), name="workspace_known_role")]


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
