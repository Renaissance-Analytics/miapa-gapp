"""Closed current-owner administration API; no generic model/financial admin."""
from django.db import DatabaseError
from django.http import JsonResponse

from backend.maipa.api import error, method_not_allowed
from backend.maipa.workspace.policy import authorize
from . import catalog, services
from .schema import InvalidFact, require
from .views import body


def clients(request):
    try:
        _, denied = authorize(request, 'workspace.read')
        if denied is not None:
            return denied
        services.scope(request.user, True)
        if request.method == 'GET':
            require(set(request.GET).issubset({'engagement_id'}) and
                    all(len(request.GET.getlist(key)) == 1 for key in request.GET))
            return JsonResponse(catalog.snapshot(request.user, request.GET.get('engagement_id')),
                                json_dumps_params={'ensure_ascii': False})
        if request.method != 'POST':
            return method_not_allowed('GET, POST')
        require(not request.GET)
        value, denied = body(request)
        if denied is not None:
            return denied
        saved = catalog.apply(request.user, request.headers.get('X-Maipa-Client-Actor'), value)
        return JsonResponse(saved, status=201 if saved['action'].startswith('create_') else 200)
    except InvalidFact:
        return error('invalid_request', 400)
    except services.ScopeDenied as failure:
        return error(failure.code, 403)
    except services.NotFound:
        return error('not_found', 404)
    except services.RevisionConflict:
        return error('revision_conflict', 409)
    except (DatabaseError, catalog.CatalogUnavailable):
        return error('client_catalog_unavailable', 503)
