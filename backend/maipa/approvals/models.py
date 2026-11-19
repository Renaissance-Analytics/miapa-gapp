"""Exact record changes the agent asks for; only the person's decision executes them."""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

STATES = ('pending', 'approved', 'rejected', 'failed')


class ActionRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    operation = models.CharField(max_length=32)
    payload = models.JSONField()
    payload_hash = models.CharField(max_length=64)
    summary = models.CharField(max_length=500)
    batch = models.UUIDField(null=True, blank=True)
    state = models.CharField(max_length=8, default='pending')
    result = models.JSONField(null=True, blank=True)
    error = models.CharField(max_length=64, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(state__in=STATES), name='approval_request_state')]
        indexes = [models.Index(fields=('user', 'state', 'created_at'), name='approval_user_state')]


class ChatAttachment(models.Model):
    """A text file the person attached in chat, kept privately for the agent to read by ID."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    name = models.CharField(max_length=128)
    content = models.TextField()
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    lines = models.PositiveIntegerField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [models.Index(fields=('user', 'created_at'), name='attachment_user_created')]
