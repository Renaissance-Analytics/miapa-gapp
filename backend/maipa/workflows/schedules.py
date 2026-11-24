"""Scheduled runs: an approved, pinned published version started by the worker once per time slot (UTC).

A schedule grants nothing by itself. Each slot is dispatched under the owner's access as it is at that moment,
with a dispatch key unique to the slot, so a slot can never start two runs. Missed slots are not made up.
"""
from calendar import monthrange
from datetime import datetime, timedelta, timezone as utc_zone

from django.db import IntegrityError, transaction
from django.utils import timezone

from . import drafts, services
from .errors import Conflict, InvalidWorkflow, Unavailable
from .models import DefinitionVersion, RunSchedule, WorkflowDraft
from .schema import bounded_json

EVERY = ('day', 'week', 'month')
MAX_ACTIVE = 20
MAX_SEED_BYTES = 2000
UTC = utc_zone.utc


class ScheduleError(ValueError):
    def __init__(self, code='workflow_schedule_invalid'):
        self.code = code
        super().__init__(code)


def require(condition, code='workflow_schedule_invalid'):
    if not condition:
        raise ScheduleError(code)


def _timing(value):
    every, hour, weekday, day = value.get('every'), value.get('hour'), value.get('weekday'), value.get('day')
    require(type(every) is str and every in EVERY and type(hour) is int and 0 <= hour <= 23)
    require((type(weekday) is int and 0 <= weekday <= 6) if every == 'week' else 'weekday' not in value)
    require((type(day) is int and 1 <= day <= 31) if every == 'month' else 'day' not in value)
    return {'every': every, 'hour': hour, 'weekday': weekday, 'day': day}


def _month_slot(timing, year, month):
    return datetime(year, month, min(timing['day'], monthrange(year, month)[1]), timing['hour'], tzinfo=UTC)


def latest_slot(timing, now):
    """The most recent slot at or before now."""
    now = now.astimezone(UTC)
    if timing['every'] == 'month':
        slot = _month_slot(timing, now.year, now.month)
        if slot > now:
            previous = now.replace(day=1) - timedelta(days=1)
            slot = _month_slot(timing, previous.year, previous.month)
        return slot
    slot = now.replace(hour=timing['hour'], minute=0, second=0, microsecond=0)
    if slot > now:
        slot -= timedelta(days=1)
    if timing['every'] == 'week':
        slot -= timedelta(days=(slot.weekday() - timing['weekday']) % 7)
    return slot


def next_slot(timing, now):
    """The first slot after now."""
    now = now.astimezone(UTC)
    if timing['every'] == 'month':
        slot = _month_slot(timing, now.year, now.month)
        if slot <= now:
            following = now.replace(day=28) + timedelta(days=4)
            slot = _month_slot(timing, following.year, following.month)
        return slot
    slot = now.replace(hour=timing['hour'], minute=0, second=0, microsecond=0)
    if slot <= now:
        slot += timedelta(days=1)
    if timing['every'] == 'week':
        slot += timedelta(days=(timing['weekday'] - slot.weekday()) % 7)
    return slot


def _row_timing(row):
    return {'every': row.every, 'hour': row.hour, 'weekday': row.weekday, 'day': row.day}


def check(user, value):
    """Validate a request against the person's own published workflow. Read-only."""
    require(type(value) is dict and {'draft_id', 'definition_revision', 'every', 'hour'} <= set(value)
            and set(value) <= {'draft_id', 'definition_revision', 'every', 'hour', 'weekday', 'day', 'seed'})
    timing = _timing(value)
    seed = value.get('seed', {})
    # Flat and small, so the approval card can show every value the runs will start with.
    require(type(seed) is dict and len(seed) <= 20 and all(type(item) in (str, int, float, bool, type(None)) for item in seed.values()))
    try:
        seed, _ = bounded_json(seed, MAX_SEED_BYTES)
    except InvalidWorkflow:
        raise ScheduleError() from None
    try:
        member = services._principal(user)
        draft = drafts.get(user, value['draft_id'])
    except (Unavailable, InvalidWorkflow, Conflict):
        raise ScheduleError('workflow_unavailable') from None
    publication = draft['publication']
    require(publication is not None and type(value['definition_revision']) is int
            and publication['definition_revision'] == value['definition_revision'], 'workflow_not_published')
    return {'member': member, 'definition_id': publication['definition_id'], 'revision': publication['definition_revision'],
            'name': draft['name'], 'timing': timing, 'seed': seed}


def _owned(user, schedule_id):
    try:
        member = services._principal(user)
        row = RunSchedule.objects.select_related('definition', 'version').filter(
            pk=services.identity(schedule_id), owner_id=member.user_id, installation_id=member.installation_id).first()
    except (Unavailable, InvalidWorkflow):
        raise ScheduleError('workflow_unavailable') from None
    require(row is not None, 'workflow_unavailable')
    return row


def request_check(user, value):
    checked = check(user, value)
    member = checked['member']
    require(RunSchedule.objects.filter(owner_id=member.user_id, installation_id=member.installation_id, stopped_at=None).count() < MAX_ACTIVE,
            'workflow_schedule_limit')
    return checked


def describe(user, value, now=None):
    checked = check(user, value)
    return {'workflow': checked['name'], 'version': checked['revision'], **checked['timing'], 'input': checked['seed'],
            'next': next_slot(checked['timing'], now or timezone.now()).isoformat(), 'current': True}


@transaction.atomic
def create(user, value):
    checked = request_check(user, value)
    member = checked['member']
    version = DefinitionVersion.objects.get(definition_id=checked['definition_id'], revision=checked['revision'])
    row = RunSchedule.objects.create(definition_id=checked['definition_id'], version=version, owner_id=member.user_id,
        installation_id=member.installation_id, seed=checked['seed'], created_at=timezone.now(), **checked['timing'])
    return {'id': str(row.pk), 'next': next_slot(checked['timing'], row.created_at).isoformat()}


def stop_check(user, schedule_id):
    row = _owned(user, schedule_id)
    require(row.stopped_at is None, 'workflow_schedule_stopped')
    return row


def describe_stop(user, schedule_id):
    row = _owned(user, schedule_id)
    return {'workflow': row.definition.name, 'version': row.version.revision, **_row_timing(row), 'input': row.seed, 'current': row.stopped_at is None}


@transaction.atomic
def stop(user, schedule_id):
    row = stop_check(user, schedule_id)
    row.stopped_at = timezone.now()
    row.save(update_fields=('stopped_at',))
    return {'id': str(row.pk), 'active': False}


@transaction.atomic
def listing(user, now=None):
    try:
        member = services._principal(user)
    except Unavailable:
        raise ScheduleError('workflow_unavailable') from None
    now = now or timezone.now()
    rows = list(RunSchedule.objects.select_related('definition', 'version').filter(
        owner_id=member.user_id, installation_id=member.installation_id).order_by('-created_at', 'id')[:100])
    known = {item.definition_id: str(item.pk) for item in WorkflowDraft.objects.filter(definition_id__in=[row.definition_id for row in rows])}
    return [{'id': str(row.pk), 'draft_id': known.get(row.definition_id), 'workflow': row.definition.name, 'version': row.version.revision,
             **_row_timing(row), 'active': row.stopped_at is None,
             'next': next_slot(_row_timing(row), now).isoformat() if row.stopped_at is None else None,
             'last_slot': row.last_slot, 'last_result': row.last_result} for row in rows]


class _NoAccess(Exception):
    pass


def _key(slot):
    return slot.strftime('%Y-%m-%dT%H')


@transaction.atomic
def _start(schedule_id, key):
    row = RunSchedule.objects.select_for_update().filter(pk=schedule_id, stopped_at=None).first()
    if row is None or row.last_slot == key:
        return None
    try:
        with transaction.atomic():
            try:
                member = services._principal(row.owner)
            except Unavailable:
                member = None
            if member is None or member.installation_id != row.installation_id:
                raise _NoAccess()
            services._dispatch_row(member, str(row.definition_id), row.version.revision, row.seed, f'schedule:{row.pk}:{key}',
                                   origin_kind='local', scheduled=True)
        result = 'started'
    except _NoAccess:
        result = 'no_access'
    except Unavailable:
        result = 'unavailable'
    except (Conflict, ValueError, IntegrityError):
        result = 'refused'
    row.last_slot, row.last_result = key, result
    row.save(update_fields=('last_slot', 'last_result'))
    return result


def run_due(now=None):
    """Start the latest due slot of every active schedule, once. Trusted worker entry; no principal comes from input."""
    now = now or timezone.now()
    counts = {'started': 0, 'skipped': 0}
    for row in RunSchedule.objects.filter(stopped_at=None).order_by('created_at', 'id')[:500]:
        slot = latest_slot(_row_timing(row), now)
        if slot <= row.created_at or row.last_slot == _key(slot):
            continue
        result = _start(row.pk, _key(slot))
        if result is not None:
            counts['started' if result == 'started' else 'skipped'] += 1
    return counts
