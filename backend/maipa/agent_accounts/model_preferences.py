"""Human future-session intent. Neither saved metadata nor a model hint grants execution."""
import uuid
from django.db import DatabaseError, transaction
from django.db.models import F
from django.http import JsonResponse
from backend.maipa.api import error, method_not_allowed
from backend.maipa.workspace.models import Installation
from backend.maipa.workspace.setup_views import payload
from . import chat
from .chat_views import _guard
from .metadata import _load as load_account, MetadataUnavailable
from .models import AccountModelPreference, ModelPreferenceEvent, ProviderProfile, Conversation


def serialize_writes():
    # Same primary authority boundary as membership/account changes; no epoch change.
    if Installation.objects.filter(slot=1).update(access_revision=F('access_revision')) != 1:
        raise MetadataUnavailable()


def _load(actor, account_id):
    account, binding, _ = load_account(actor, account_id)
    row = AccountModelPreference.objects.filter(account=account).first()
    profile = ProviderProfile.objects.filter(account=account).first()
    if row:
        if (not profile or row.profile_id != profile.pk
                or (row.user_id, row.account_id, row.installation_id, str(row.storage_id), row.provider) != binding):
            raise MetadataUnavailable()
        if ((row.model_id is None) != (row.model_name is None)
                or row.issue not in (None, 'missing', 'unconfirmed')
                or (row.model_id is None and row.issue is not None)):
            raise MetadataUnavailable()
        if row.model_id is not None:
            from backend.maipa.acp import model_catalog
            model_catalog({'currentModelId': row.model_id, 'availableModels': [{'modelId': row.model_id, 'name': row.model_name}]})
    return account, binding, profile, row


def _public(account, binding, profile, row):
    return {'user_id': str(binding[0]), 'account_id': str(account.pk), 'provider': binding[4],
            'revision': row.revision if row else 0, 'model_id': row.model_id if row else None,
            'model_name': row.model_name if row else None, 'issue': row.issue if row else None}


@transaction.atomic
def read(actor, account_id):
    return _public(*_load(actor, account_id))


@transaction.atomic
def save(actor, account_id, value, session_key):
    if type(value) is not dict:
        raise ValueError('Invalid model preference')
    mode = value.get('mode')
    keys = {'mode', 'expected_revision'}
    if mode == 'current_model':
        keys |= {'selection_revision', 'conversation_revision', 'conversation_id'}
    elif mode != 'provider_default':
        raise ValueError('Invalid model preference')
    if set(value) != keys:
        raise ValueError('Invalid model preference')
    for field in ('expected_revision', 'selection_revision', 'conversation_revision'):
        if field in value and (type(value[field]) is not int or not 0 <= value[field] < 2_000_000_000):
            raise ValueError('Invalid preference revision')
    if mode == 'current_model':
        identity = value['conversation_id']
        if type(identity) is not str or str(uuid.UUID(identity)) != identity:
            raise ValueError('Invalid conversation')
    serialize_writes()
    account, binding, profile, row = _load(actor, account_id)
    revision = row.revision if row else 0
    if revision != value['expected_revision']:
        raise chat.ChatConflict('Preference changed')
    model_id, name = None, None
    if mode == 'current_model':
        choice = chat._lock_selection(actor, value['selection_revision'])
        if choice.account_id != account.pk:
            raise chat.ChatConflict('Conversation changed')
        conversation = chat._conversation(actor, account)
        if (not conversation or str(conversation.pk) != identity or conversation.phase != 'ready'
                or any(m.get('state') in ('queued', 'streaming') for m in conversation.messages)
                or Conversation.objects.filter(pk=identity, revision=value['conversation_revision'], phase='ready').update(revision=F('revision')) != 1):
            raise chat.ChatConflict('Conversation changed')
        from .chat_runtime import confirmed_current_model
        model_id, name = confirmed_current_model(actor, account, conversation, session_key)
        if not profile:
            raise MetadataUnavailable()
    before = {'model_id': row.model_id if row else None, 'model_name': row.model_name if row else None}
    after = {'model_id': model_id, 'model_name': name}
    if before == after:
        return _public(account, binding, profile, row)
    if revision >= 1_999_999_999:
        raise chat.ChatConflict('Preference changed')
    if row:
        row.model_id, row.model_name, row.issue, row.revision = model_id, name, None, revision + 1
        row.save(update_fields=['model_id', 'model_name', 'issue', 'revision'])
    else:
        row = AccountModelPreference.objects.create(account=account, profile=profile, user_id=binding[0],
            installation_id=binding[2], storage_id=binding[3], provider=binding[4], revision=1, **after)
    ModelPreferenceEvent.objects.create(account=account, profile=profile, actor_id=binding[0], installation_id=binding[2],
        storage_id=binding[3], revision=row.revision, before=before, after=after)
    return _public(account, binding, profile, row)


@transaction.atomic
def connect_intent(actor, account_id):
    return read(actor, account_id)


@transaction.atomic
def check_intent(actor, account_id, revision, issue=None):
    serialize_writes()
    account, binding, profile, row = _load(actor, account_id)
    if (row.revision if row else 0) != revision:
        raise chat.ChatConflict('Preference changed')
    if row and row.model_id is not None and row.issue != issue:
        row.issue = issue
        row.save(update_fields=['issue'])


def model_preference(request, account_id):
    denied = _guard(request)
    if denied is not None:
        return denied
    if request.method not in ('GET', 'POST'):
        return method_not_allowed('GET, POST')
    try:
        if request.method == 'GET':
            return JsonResponse(read(request.user, account_id))
        value, denied = payload(request)
        if denied is not None:
            return denied
        return JsonResponse(save(request.user, account_id, value, request.session.session_key))
    except chat.ChatConflict:
        return error('model_preference_conflict', 409)
    except MetadataUnavailable:
        return error('agent_account_unavailable', 404)
    except (ValueError, UnicodeError, RecursionError):
        return error('invalid_request', 400)
    except (DatabaseError, chat.ChatUnavailable):
        return error('model_preference_unavailable', 503)
