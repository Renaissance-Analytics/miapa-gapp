"""Server-owned personal pane widths with identity and compare-and-swap guards."""
from django.db import IntegrityError, OperationalError
from django.db.models import F
from django.http import JsonResponse
from django.views.decorators.debug import sensitive_variables

from backend.maipa.api import error, method_not_allowed
from backend.maipa.intake.schema import InvalidFact, require
from .models import WorkspaceLayout
from .policy import authorize
from .setup_views import payload


def snapshot(row):
    return {'layout_id': str(row.identity), 'user_id': str(row.user_id),
            'revision': row.revision, 'split_basis_points': row.split_basis_points}


@sensitive_variables('value')
def personal_layout(request):
    member, denied = authorize(request, 'workspace.read')
    if denied is not None:
        return denied
    if request.method not in ('GET', 'POST'):
        return method_not_allowed('GET, POST')
    try:
        if request.method == 'GET':
            row, _ = WorkspaceLayout.objects.get_or_create(user=request.user,
                defaults={'installation': member.installation})
            if row.installation_id != member.installation_id:
                return error('workspace_access_denied', 403)
        else:
            value, denied = payload(request)
            if denied is not None:
                return denied
            require(set(value) == {'expected_revision', 'split_basis_points'})
            revision, points = value['expected_revision'], value['split_basis_points']
            require(type(revision) is int and 0 <= revision < 1_999_999_999)
            require(type(points) is int and 2200 <= points <= 4500)
            # No client-selected owner. A previous tab's layout identity cannot
            # write a different principal's layout even at a matching revision.
            rows = WorkspaceLayout.objects.filter(user=request.user, installation=member.installation)
            row = rows.first()
            if row is None or request.headers.get('X-Maipa-Layout') != str(row.identity):
                return error('revision_conflict', 409)
            if rows.filter(pk=row.pk, revision=revision).update(
                    split_basis_points=points, revision=F('revision') + 1) != 1:
                return error('revision_conflict', 409)
            # Return this accepted revision, never an unrelated later writer.
            row.split_basis_points, row.revision = points, revision + 1
        return JsonResponse(snapshot(row))
    except (OperationalError, IntegrityError):
        return error('layout_unavailable', 503)
    except (InvalidFact, ValueError, UnicodeError, RecursionError):
        return error('invalid_request', 400)
