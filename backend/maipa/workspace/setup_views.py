"""Strict JSON onboarding routes, sharing the established session/CSRF boundary."""
import json

from django.contrib.auth import login
from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables

from backend.maipa.api import error, method_not_allowed
from backend.maipa.intake.schema import InvalidFact, require
from backend.maipa.intake import services
from . import bootstrap, onboarding
from .policy import authorize
from .views import _unique_object


def payload(request):
    require(request.content_type == 'application/json')
    raw = request.read(4097)
    if len(raw) > 4096:
        return None, error('request_too_large', 413)
    def constant(_):
        raise ValueError('Invalid JSON')
    value = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=constant)
    require(isinstance(value, dict))
    return value, None


@sensitive_post_parameters()
@sensitive_variables('value', 'user')
def first_run(request):
    if request.method not in ('GET', 'POST'):
        return method_not_allowed('GET, POST')
    try:
        if request.method == 'GET':
            return JsonResponse({'setup_required': bootstrap.required()})
        if request.user.is_authenticated or not bootstrap.required():
            return error('setup_closed', 409)
        value, denied = payload(request)
        if denied is not None:
            return denied
        user = bootstrap.claim(value)
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        return JsonResponse({'user': {'id': str(user.pk)}, 'csrf_token': get_token(request)}, status=201)
    except bootstrap.SetupClosed:
        return error('setup_closed', 409)
    except bootstrap.InvalidCode:
        return error('invalid_setup_code', 403)
    except (bootstrap.SetupUnavailable, OperationalError):
        return error('setup_unavailable', 503)
    except IntegrityError:
        return error('setup_closed', 409)
    except (ValueError, ValidationError, UnicodeError, RecursionError):
        return error('invalid_request', 400)


@sensitive_post_parameters()
@sensitive_variables('value')
def account_setup(request):
    _, denied = authorize(request, 'workspace.read')
    if denied is not None:
        return denied
    if request.method not in ('GET', 'POST'):
        return method_not_allowed('GET, POST')
    try:
        if request.method == 'GET':
            row = onboarding.current(request.user)
        else:
            value, denied = payload(request)
            if denied is not None:
                return denied
            row = onboarding.respond(request.user, value, request.headers.get('X-Maipa-Setup'))
        return JsonResponse(onboarding.snapshot(request.user, row))
    except services.ScopeDenied as exc:
        return error(exc.code, 403)
    except services.NotFound:
        return error('not_found', 404)
    except services.RevisionConflict:
        return error('revision_conflict', 409)
    except (OperationalError, IntegrityError):
        return error('setup_unavailable', 503)
    except (InvalidFact, ValueError, UnicodeError, RecursionError):
        return error('invalid_request', 400)
