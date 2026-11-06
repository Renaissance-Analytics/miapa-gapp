"""Durable account setup state.

The connected in-app agent guides this setup through scoped tools; people can also
answer each step directly. Nothing here pretends to be a model, and only a human
confirmation saves the reviewed setup into client records.
"""
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from backend.maipa.intake import services
from backend.maipa.intake.models import Entity
from backend.maipa.intake.schema import day, fields, require, text, validate_value
from .models import AccountOnboarding


PROMPTS = {
    'display_name': 'Welcome to Maipa. What should I call you?',
    'workspace': 'Would you like to set up a new client, use one of your existing entities, or do this later?',
    'engagement_name': 'What would you like to call this client engagement?',
    'entity_name': 'What is the name of the business or entity?',
    'currency': 'Which three-letter currency code do these books use, for example USD?',
    'reporting_basis': 'Which reporting basis do you use? You can leave this unknown.',
    'effective_from': 'From which date do these currency and reporting-basis answers apply?',
    'review': 'Review your setup below. Confirm to save it, or restart this draft to make changes.',
    'complete': 'Your confirmed account setup is saved. Your client facts are available in the work area.',
}


# What each step is for, shown beside the question so nobody has to guess.
EXPLANATIONS = {
    'display_name': ('Your name', 'How Maipa addresses you. It is saved to your own user profile only; it is not a client or business name.'),
    'workspace': ('Choose a client', 'Maipa keeps each client’s books separate. Start a new client, pick an entity you already set up, or skip this for now and finish later.'),
    'engagement_name': ('Engagement', 'An engagement is the work you do for a client, for example “Acme 2026 bookkeeping”. It groups that client’s entities, facts and workpapers.'),
    'entity_name': ('Entity', 'The business or person whose books these are, for example “Acme LLC”. Records and facts are kept separately for each entity.'),
    'currency': ('Currency', 'The three-letter code of the currency the books are kept in, for example USD. Every amount for this entity uses it.'),
    'reporting_basis': ('Reporting basis', 'Cash basis records income and expenses when money moves; accrual records them when earned or owed. Choose “not sure yet” if you do not know.'),
    'effective_from': ('Applies from', 'The date from which the currency and reporting basis are true. Maipa keeps dated history, so a later change never overwrites an earlier fact.'),
    'review': ('Review and confirm', 'Nothing has been saved to client records yet. Check the summary, then confirm to save it or restart to change it.'),
    'complete': ('Done', 'Your setup is saved. Client facts record this confirmed setup as their source.'),
}
AGENT_SETUP_STEPS = ('display_name', 'workspace', 'engagement_name', 'entity_name', 'currency', 'reporting_basis', 'effective_from')


def path(row, editor):
    """The steps this person will actually pass through, given their answers so far."""
    steps = ['display_name']
    if editor:
        steps.append('workspace')
        choice = row.draft.get('workspace')
        if choice != 'later':
            if choice in (None, 'new'):
                steps += ['engagement_name', 'entity_name']
            steps += ['currency', 'reporting_basis', 'effective_from']
    return steps + ['review', 'complete']


def current(user):
    member = services.scope(user)
    row, _ = AccountOnboarding.objects.get_or_create(user=user, defaults={
        'installation': member.installation,
        'messages': [{'role': 'guide', 'text': PROMPTS['display_name']}]})
    if row.installation_id != member.installation_id:
        raise services.ScopeDenied()
    return row


def snapshot(user, row):
    member = services.scope(user)
    choices = []
    if row.step == 'workspace' and member.role in ('administrator', 'preparer'):
        choices = [{'value': 'new', 'label': 'Set up a new client'}, {'value': 'later', 'label': 'Do this later'}]
        # Existing intake quotas bound this list to 100 engagements x 100 entities.
        entities = Entity.objects.filter(engagement__in=services.owned_engagements(user)).select_related('engagement').order_by('engagement__created_at', 'created_at', 'id')
        choices += [{'value': str(entity.pk), 'label': f'{entity.engagement.name} / {entity.name}'} for entity in entities]
    elif row.step == 'reporting_basis':
        choices = [{'value': value, 'label': label} for value, label in (
            ('cash', 'Cash'), ('accrual', 'Accrual'), ('other', 'Other'), ('unknown', 'I’m not sure yet'))]
    return {'setup_id': str(row.identity), 'revision': row.revision, 'step': row.step, 'draft': row.draft, 'messages': row.messages,
            'choices': choices, 'entity_id': str(row.entity_id) if row.entity_id else None,
            'deferred': row.deferred, 'provider_ready': False,
            'can_edit': member.role in ('administrator', 'preparer'),
            'question': PROMPTS[row.step], 'explanation': EXPLANATIONS[row.step][1],
            'steps': [{'id': key, 'title': EXPLANATIONS[key][0], 'explanation': EXPLANATIONS[key][1]}
                      for key in path(row, member.role in ('administrator', 'preparer'))]}


def _advance(user, row, answer, source='user'):
    draft, step = dict(row.draft), row.step
    label = answer
    if step == 'display_name':
        draft['display_name'] = text(answer, 128)
        step = 'workspace' if services.scope(user).role in ('administrator', 'preparer') else 'review'
    elif step == 'workspace':
        services.scope(user, True)
        if answer == 'new':
            draft['workspace'] = 'new'
            step, label = 'engagement_name', 'Set up a new client'
        elif answer == 'later':
            draft['workspace'] = 'later'
            step, label = 'review', 'Do this later'
        else:
            require(isinstance(answer, str))
            try:
                import uuid
                identity = uuid.UUID(answer)
            except ValueError:
                require(False)
            entity = services.owned_entity(user, identity, True)
            draft.update(workspace='existing', entity_id=str(entity.pk), entity_revision=entity.revision,
                         entity_name=entity.name, engagement_name=entity.engagement.name)
            step, label = 'currency', f'{entity.engagement.name} / {entity.name}'
    elif step in ('engagement_name', 'entity_name'):
        draft[step] = text(answer)
        step = 'entity_name' if step == 'engagement_name' else 'currency'
    elif step == 'currency':
        validate_value('currency', answer)
        draft['currency'] = answer
        step = 'reporting_basis'
    elif step == 'reporting_basis':
        if answer != 'unknown':
            validate_value('reporting_basis', answer)
        draft['reporting_basis'] = None if answer == 'unknown' else answer
        step = 'effective_from'
        label = 'Unknown for now' if answer == 'unknown' else answer.title()
    elif step == 'effective_from':
        day(answer)
        draft['effective_from'] = answer
        step = 'review'
    elif step == 'review':
        require(answer == 'confirm')
        if draft.get('workspace') in ('new', 'existing'):
            services.scope(user, True)
            if draft['workspace'] == 'new':
                engagement = services.create_engagement(user, {'name': draft['engagement_name'], 'entity_name': draft['entity_name']})
                entity = engagement.entities.get()
                revision = entity.revision
            else:
                entity = services.owned_entity(user, draft['entity_id'], True)
                revision = draft['entity_revision']
            for key in ('currency', 'reporting_basis'):
                value = draft[key]
                fact = services.record_fact(user, entity.pk, {'key': key, 'knowledge': 'unknown' if value is None else 'known',
                    'value': value, 'expected_revision': revision, 'effective_from': draft['effective_from'],
                    'effective_to': None, 'evidence': {'reference': 'Account onboarding: user-confirmed setup',
                        'locator': f'Account setup revision {row.revision + 1}', 'observed_on': timezone.now().date().isoformat()}})
                revision = fact.revision
            row.entity = entity
        user.first_name = draft['display_name']
        user.save(update_fields=['first_name'])
        row.deferred = draft.get('workspace') == 'later'
        step, label = 'complete', 'Confirm and save setup'
    else:
        require(False)
    row.draft, row.step = draft, step
    row.messages = row.messages + [{'role': source, 'text': label}, {'role': 'guide', 'text': PROMPTS[step]}]
    require(len(row.messages) <= 19)


@transaction.atomic
def respond(user, value, setup_id, source='user'):
    fields(value, ('expected_revision', 'action', 'answer'))
    revision, action, answer = value['expected_revision'], value['action'], value['answer']
    require(type(revision) is int and 0 <= revision < 1_999_999_999)
    require(isinstance(answer, str) and len(answer) <= 128)
    require(action in ('reply', 'reset', 'resume'))
    row = current(user)
    if row.revision != revision or str(row.identity) != setup_id:
        raise services.RevisionConflict()
    # Take the write boundary before touching any profile or intake record.
    if not AccountOnboarding.objects.filter(pk=row.pk, revision=revision).update(revision=F('revision') + 1):
        raise services.RevisionConflict()
    if action == 'reset':
        require(answer == '' and row.step != 'complete')
        row.step, row.draft = 'display_name', {}
        row.messages = [{'role': 'guide', 'text': PROMPTS['display_name']}]
    elif action == 'resume':
        require(answer == '' and row.step == 'complete' and row.deferred)
        services.scope(user, True)
        row.step, row.deferred = 'workspace', False
        row.draft = {'display_name': row.draft['display_name']}
        row.messages = row.messages[:3]
        row.messages[-1] = {'role': 'guide', 'text': PROMPTS['workspace']}
    else:
        _advance(user, row, answer, source)
    row.revision = revision + 1
    row.save(update_fields=['revision', 'step', 'draft', 'messages', 'entity', 'deferred'])
    return row


class HumanConfirmationRequired(Exception):
    """The reviewed setup is saved only by the person's own confirmation control."""


def agent_read(user):
    return snapshot(user, current(user))


def agent_record(user, setup_id, expected_revision, action, answer):
    """Record an answer the person gave in conversation. Never confirms the save."""
    row = current(user)
    if action == 'reply' and row.step not in AGENT_SETUP_STEPS:
        raise HumanConfirmationRequired()
    return snapshot(user, respond(user, {'expected_revision': expected_revision, 'action': action, 'answer': answer},
                                  setup_id, source='agent'))
