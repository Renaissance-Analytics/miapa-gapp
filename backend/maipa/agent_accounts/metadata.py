"""Owned human display metadata, independent of connection/session revisions."""
from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.db.models import F
from django.http import JsonResponse

from backend.maipa.api import error, method_not_allowed
from backend.maipa.workspace.models import Installation
from backend.maipa.workspace.setup_views import payload
from .chat_views import _guard
from .models import AccountMetadataEvent, AgentAccount, ProviderProfile
from .references import _binding, ReferenceUnavailable


class MetadataUnavailable(Exception):
    pass


class MetadataConflict(Exception):
    pass


def _label(value):
    if (type(value) is not str or not value or value != value.strip()
            or len(value.encode('utf-16-le')) > 160
            or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value)):
        raise ValueError('Invalid account label')
    return value


def _load(actor, account_id):
    try:
        binding = _binding(actor, str(account_id))
        account = AgentAccount.objects.get(pk=account_id, owner=actor, enabled=True)
        profile = ProviderProfile.objects.filter(account=account).first()
        if profile and (profile.user_id, profile.account_id, profile.installation_id,
                        str(profile.storage_id), profile.provider) != binding:
            raise MetadataUnavailable()
        _label(account.label)
        return account, binding, profile is not None
    except (ObjectDoesNotExist, ReferenceUnavailable, ValueError, OSError, TypeError, AttributeError):
        raise MetadataUnavailable() from None


def _public(account, binding, assigned):
    return {'user_id': str(binding[0]), 'account_id': str(account.pk),
            'provider': account.provider, 'label': account.label,
            'profile_assigned': assigned, 'revision': account.metadata_revision}


@transaction.atomic
def read(actor, account_id):
    return _public(*_load(actor, account_id))


@transaction.atomic
def rename(actor, account_id, value):
    if type(value) is not dict or set(value) != {'expected_revision', 'label'}:
        raise ValueError('Invalid account metadata')
    revision, label = value['expected_revision'], _label(value['label'])
    if type(revision) is not int or not 0 <= revision < 2_000_000_000:
        raise ValueError('Invalid metadata revision')
    # Share the current user/role write boundary without advancing access or
    # connection authority. The metadata revision alone changes below.
    if Installation.objects.filter(slot=1).update(access_revision=F('access_revision')) != 1:
        raise MetadataUnavailable()
    account, binding, assigned = _load(actor, account_id)
    if account.metadata_revision != revision:
        raise MetadataConflict()
    if account.label == label:
        return _public(account, binding, assigned)
    if AgentAccount.objects.filter(pk=account.pk, owner_id=actor.pk, owner__is_active=True,
            enabled=True, metadata_revision=revision, metadata_revision__lt=1_999_999_999).update(
                label=label, metadata_revision=F('metadata_revision') + 1) != 1:
        raise MetadataConflict()
    changed, current_binding, assigned = _load(actor, account_id)
    if current_binding != binding or (changed.label, changed.metadata_revision) != (label, revision + 1):
        raise MetadataUnavailable()
    AccountMetadataEvent.objects.create(account=changed, actor_id=binding[0],
        installation_id=binding[2], storage_id=binding[3], revision=changed.metadata_revision,
        before_label=account.label, after_label=changed.label)
    return _public(changed, current_binding, assigned)


def metadata(request, account_id):
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
        return JsonResponse(rename(request.user, account_id, value))
    except MetadataConflict:
        return error('account_metadata_conflict', 409)
    except MetadataUnavailable:
        return error('agent_account_unavailable', 404)
    except (ValueError, UnicodeError, RecursionError):
        return error('invalid_request', 400)
    except DatabaseError:
        return error('account_metadata_unavailable', 503)
