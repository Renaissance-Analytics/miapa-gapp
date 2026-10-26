"""Private engagement/entity records and append-only application fact history."""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from .schema import KEYS

CATALOG_ACTIONS = ('create_engagement', 'create_entity', 'rename_engagement', 'rename_entity')


class ClientCatalogState(models.Model):
    identity = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, editable=False)
    installation = models.ForeignKey('workspace.Installation', on_delete=models.PROTECT, editable=False)
    revision = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(revision__lt=2_000_000_000),
                                              name='intake_catalog_revision')]


class ClientCatalogEvent(models.Model):
    catalog = models.ForeignKey(ClientCatalogState, on_delete=models.PROTECT, editable=False)
    revision = models.PositiveIntegerField(editable=False)
    action = models.CharField(max_length=24, editable=False)
    target_id = models.UUIDField(editable=False)
    engagement_id = models.UUIDField(editable=False)
    at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=('catalog', 'revision'), name='intake_catalog_event_revision'),
            models.CheckConstraint(condition=models.Q(action__in=CATALOG_ACTIONS), name='intake_catalog_event_action'),
            models.CheckConstraint(condition=models.Q(revision__gt=0, revision__lt=2_000_000_000),
                                   name='intake_catalog_event_bounds'),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError('Client catalog events are append-only')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('Client catalog events are append-only')


class Engagement(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    installation = models.ForeignKey('workspace.Installation', on_delete=models.PROTECT)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    name = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)


class Entity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    engagement = models.ForeignKey(Engagement, related_name='entities', on_delete=models.PROTECT)
    name = models.CharField(max_length=128)
    revision = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)


class FactRevision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.ForeignKey(Entity, related_name='fact_revisions', on_delete=models.PROTECT)
    key = models.CharField(max_length=32, choices=[(key, key) for key in KEYS])
    revision = models.PositiveIntegerField()
    knowledge = models.CharField(max_length=7, choices=[('known', 'Known'), ('unknown', 'Unknown')])
    value = models.JSONField(null=True)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True)
    evidence = models.JSONField()
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['entity', 'revision'], name='intake_unique_entity_revision'),
            models.CheckConstraint(condition=models.Q(revision__gte=1), name='intake_positive_revision'),
            models.CheckConstraint(condition=models.Q(key__in=KEYS), name='intake_known_fact_key'),
            models.CheckConstraint(condition=(models.Q(knowledge='unknown', value__isnull=True)
                | models.Q(knowledge='known', value__isnull=False)), name='intake_fact_knowledge_value'),
            models.CheckConstraint(condition=(models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gt=models.F('effective_from'))), name='intake_fact_effective_interval'),
        ]
        indexes = [models.Index(fields=['entity', 'key', '-revision'], name='intake_fact_history')]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError('Fact revisions are append-only')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('Fact revisions are append-only')


class Conclusion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.ForeignKey(Entity, related_name='conclusions', on_delete=models.PROTECT)
    label = models.CharField(max_length=128)
    as_of = models.DateField()
    status = models.CharField(max_length=7, default='current')
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    recorded_at = models.DateTimeField(auto_now_add=True)
    invalidated_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=(models.Q(status='current', invalidated_at__isnull=True)
            | models.Q(status='stale', invalidated_at__isnull=False)), name='intake_conclusion_state')]


class FactDependency(models.Model):
    conclusion = models.ForeignKey(Conclusion, related_name='dependencies', on_delete=models.PROTECT)
    fact_revision = models.ForeignKey(FactRevision, on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['conclusion', 'fact_revision'], name='intake_unique_fact_dependency')]
