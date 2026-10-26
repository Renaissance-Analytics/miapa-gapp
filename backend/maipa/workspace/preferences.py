"""Bounded personal appearance/view choices, never provider or financial state."""
from django.db import IntegrityError, OperationalError
from django.db.models import F
from django.http import JsonResponse
from django.views.decorators.debug import sensitive_variables

from backend.maipa.api import error, method_not_allowed
from backend.maipa.intake.schema import InvalidFact, require
from .models import WorkspacePreferences
from .policy import authorize
from .setup_views import payload


def snapshot(row):
    return {'preferences_id': str(row.identity), 'user_id': str(row.user_id),
            'revision': row.revision, 'theme': row.theme, 'active_view': row.active_view}


@sensitive_variables('value')
def personal_preferences(request):
    member, denied = authorize(request, 'workspace.read')
    if denied is not None:
        return denied
    if request.method not in ('GET', 'POST'):
        return method_not_allowed('GET, POST')
    try:
        if request.method == 'GET':
            row, _ = WorkspacePreferences.objects.get_or_create(user=request.user,
                defaults={'installation': member.installation})
            if row.installation_id != member.installation_id:
                return error('workspace_access_denied', 403)
        else:
            value, denied = payload(request)
            if denied is not None:
                return denied
            require(set(value) == {'expected_revision', 'theme', 'active_view'})
            revision, theme, view = value['expected_revision'], value['theme'], value['active_view']
            require(type(revision) is int and 0 <= revision < 1_999_999_999)
            require(theme in ('system', 'light', 'dark'))
            require(view in ('client-facts', 'workspace-guide'))
            rows = WorkspacePreferences.objects.filter(user=request.user, installation=member.installation)
            row = rows.first()
            if row is None or request.headers.get('X-Maipa-Preferences') != str(row.identity):
                return error('revision_conflict', 409)
            if rows.filter(pk=row.pk, revision=revision).update(theme=theme, active_view=view,
                    revision=F('revision') + 1) != 1:
                return error('revision_conflict', 409)
            row.theme, row.active_view, row.revision = theme, view, revision + 1
        return JsonResponse(snapshot(row))
    except (OperationalError, IntegrityError):
        return error('preferences_unavailable', 503)
    except (InvalidFact, ValueError, UnicodeError, RecursionError):
        return error('invalid_request', 400)
