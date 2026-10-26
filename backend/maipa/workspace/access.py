"""Local administrator controls with one serialized access revision and safe audit."""
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F
from django.http import JsonResponse
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables

from backend.maipa.api import error, method_not_allowed
from backend.maipa.intake.schema import InvalidFact, require
from .models import AccessEvent, Installation, Membership, ROLES
from .policy import authorize
from .setup_views import payload


class Refused(Exception):
    def __init__(self, code, status):
        self.code, self.status = code, status


def administrator(user_id, installation_id):
    return Membership.objects.filter(user_id=user_id, user__is_active=True,
        installation_id=installation_id, installation__slot=1, enabled=True, role='administrator').exists()


def snapshot(member):
    # One transaction gives the list/history the same revision. Recheck database
    # authority here as well as at the HTTP boundary; never trust cached User flags.
    with transaction.atomic():
        if not administrator(member.user_id, member.installation_id):
            raise Refused('permission_denied', 403)
        installation = Installation.objects.get(pk=member.installation_id)
        rows = list(Membership.objects.filter(installation=installation).select_related('user').order_by('user_id')[:201])
        if len(rows) > 200:
            raise Refused('access_unavailable', 503)
        events = AccessEvent.objects.filter(installation=installation).order_by('-revision')[:20]
        return {'actor_id': str(member.user_id), 'installation_id': str(installation.pk),
            'revision': installation.access_revision,
            'users': [{'id': str(row.user_id), 'username': row.user.username, 'role': row.role,
                       'enabled': row.enabled, 'active': row.user.is_active} for row in rows],
            'events': [{'actor_id': str(event.actor_id), 'subject_id': str(event.subject_id),
                        'kind': event.kind, 'role': event.role, 'enabled': event.enabled,
                        'revision': event.revision, 'at': event.at.isoformat()} for event in events]}


@sensitive_variables('value', 'user', 'password')
def change(member, actor_header, value, target):
    fields = {'expected_revision', 'installation_id', 'role'}
    fields |= {'username', 'password', 'confirmation'} if target is None else {'enabled'}
    require(set(value) == fields)
    revision, role = value['expected_revision'], value['role']
    require(type(revision) is int and 0 <= revision < 2_000_000_000)
    require(isinstance(role, str) and role in ROLES)
    if actor_header != str(member.user_id) or value['installation_id'] != str(member.installation_id):
        raise Refused('revision_conflict', 409)
    user = None
    if target is None:
        username, password = value['username'], value['password']
        require(isinstance(username, str) and 1 <= len(username) <= 150)
        require(isinstance(password, str) and 12 <= len(password) <= 1024 and '\0' not in password)
        require(isinstance(value['confirmation'], str) and password == value['confirmation'])
        User = get_user_model()
        user = User(username=User.normalize_username(username))
        user.full_clean(exclude=('password',))
        validate_password(password, user)
        user.set_password(password)
    else:
        require(type(value['enabled']) is bool)
    with transaction.atomic():
        # Acquire SQLite's write boundary before any read/check dependent on other
        # access mutations. This also serializes last-admin and member-limit checks.
        claimed = Installation.objects.filter(pk=member.installation_id, slot=1,
            access_revision=revision, access_revision__lt=1_999_999_999).update(access_revision=F('access_revision') + 1)
        if not claimed:
            raise Refused('revision_conflict', 409)
        if not administrator(member.user_id, member.installation_id):
            raise Refused('permission_denied', 403)
        rows = Membership.objects.filter(installation_id=member.installation_id)
        if target is None:
            if rows.count() >= 200:
                raise Refused('member_limit', 409)
            user.save(force_insert=True)
            row = Membership.objects.create(user=user, installation_id=member.installation_id, role=role)
            kind = 'created'
        else:
            row = rows.filter(user_id=target).first()
            if row is None:
                raise Refused('not_found', 404)
            if row.role == 'administrator' and row.enabled and (role != 'administrator' or not value['enabled']):
                if not rows.filter(role='administrator', enabled=True, user__is_active=True).exclude(pk=row.pk).exists():
                    raise Refused('last_administrator', 409)
            row.role, row.enabled = role, value['enabled']
            row.save(update_fields=('role', 'enabled'))
            kind = 'changed'
        AccessEvent.objects.create(installation_id=member.installation_id, actor_id=member.user_id,
            subject_id=row.user_id, kind=kind, role=row.role, enabled=row.enabled, revision=revision + 1)
    # No list is returned after self-demotion. The next read must authorize anew.
    return {'status': 'saved', 'revision': revision + 1}


@sensitive_post_parameters()
@sensitive_variables('value')
def users(request, target=None):
    try:
        member, denied = authorize(request, 'workspace.read')
        if denied is not None:
            return denied
        if member.role != 'administrator':
            return error('permission_denied', 403)
        allowed = ('GET', 'POST') if target is None else ('POST',)
        if request.method not in allowed:
            return method_not_allowed(', '.join(allowed))
        if request.method == 'GET':
            return JsonResponse(snapshot(member))
        value, denied = payload(request)
        if denied is not None:
            return denied
        result = change(member, request.headers.get('X-Maipa-Admin'), value, target)
        return JsonResponse(result, status=201 if target is None else 200)
    except Refused as exc:
        return error(exc.code, exc.status)
    except ValidationError:
        return error('invalid_request', 400)
    except (IntegrityError, OperationalError):
        return error('access_unavailable', 503)
    except (InvalidFact, ValueError, UnicodeError, RecursionError):
        return error('invalid_request', 400)
